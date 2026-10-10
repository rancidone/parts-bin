"""HTTP adapter tests for the gateway and inventory surfaces."""

from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import os

import pytest
from starlette.testclient import TestClient

with TemporaryDirectory() as setup_dir:
    config = Path(setup_dir) / "test.toml"
    config.write_text(f'[db]\npath = "{setup_dir}/parts.db"\n')
    with patch.dict(os.environ, {"PARTS_BIN_CONFIG": str(config)}):
        import server
from db.persistence import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "parts.db"
    init_db(db_path)
    from agent_runtime import AgentGateway, ConversationStore
    store = ConversationStore(db_path)
    gateway = AgentGateway(store, server._make_agent_runtime)
    with patch.object(server, "_DB_PATH", db_path), patch.object(server, "_conversation_store", store), patch.object(server, "_agent_gateway", gateway):
        with TestClient(server.app, raise_server_exceptions=True) as test_client:
            yield test_client, db_path


def test_health_reports_agent_configuration(client):
    response = client[0].get("/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "agent_configured"}


def test_agent_thread_uses_openai_without_a_picker(client):
    response = client[0].post("/agent/threads")
    assert response.status_code == 200
    thread_id = response.json()["thread_id"]
    assert server._conversation_store.runtime_for(thread_id) == "openai"


@pytest.mark.parametrize("runtime", ["codex", "local", "openai", "unsupported"])
def test_agent_thread_runtime_selection_is_rejected(client, runtime):
    response = client[0].post("/agent/threads", json={"runtime": runtime})
    assert response.status_code == 422


def test_catalog_endpoints_are_removed(client):
    assert client[0].get("/jlcparts/status").status_code == 404
    assert client[0].post("/jlcparts/download").status_code == 405


@pytest.mark.parametrize("runtime", ["codex", "local"])
def test_historical_conversations_are_readable_but_cannot_continue(client, runtime):
    import sqlite3
    with sqlite3.connect(client[1]) as conn:
        conn.execute("INSERT INTO agent_threads VALUES (?, ?)", ("old-thread", runtime))
        conn.execute("INSERT INTO agent_events VALUES (?, ?, ?, ?, ?)",
                     ("old-thread", 1, "assistant_text", runtime, '{"text":"preserved history"}'))
    assert "preserved history" in client[0].get("/agent/threads/old-thread/events").text
    assert client[0].post("/agent/threads/old-thread/messages", data={"message": "continue"}).status_code == 409
    assert client[0].post("/agent/threads/old-thread/approvals", json={"request_id": "old", "approved": True}).status_code == 409
    assert server._conversation_store.runtime_for("old-thread") == runtime


def test_inventory_still_opens(client):
    response = client[0].get("/inventory")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_message_endpoint_returns_sse_before_turn_finishes(tmp_path, monkeypatch):
    import asyncio
    from agent_runtime import AgentGateway, ApprovalEngine, ConversationStore, ModelTurn, OpenAIResponsesRuntime, ToolCall
    from domain import PartsBinService
    from tools import PartsBinToolRegistry
    release = asyncio.Event()
    stopped = asyncio.Event()

    class PausedTransport:
        calls = 0
        async def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelTurn(tool_calls=(ToolCall("search_parts", {}, "search"),))
            try:
                await release.wait()
                return ModelTurn("done")
            finally:
                stopped.set()

    store = ConversationStore(tmp_path / "conversation.db")
    gateway = AgentGateway(store, lambda: OpenAIResponsesRuntime(PausedTransport(),
        registry=PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db")), store=store, approvals=ApprovalEngine()))
    monkeypatch.setattr(server, "_agent_gateway", gateway)
    thread = gateway.create_thread()
    response = await asyncio.wait_for(server.submit_agent_message(thread, "search", None), 1)
    assert response.headers["x-accel-buffering"] == "no"
    for kind in ["user_message", "tool_call", "tool_result"]:
        chunk = await asyncio.wait_for(anext(response.body_iterator), 1)
        assert f'"kind": "{kind}"' in chunk
    assert not release.is_set()
    # Disconnecting closes the producer, including a still-pending model call.
    await response.body_iterator.aclose()
    await asyncio.wait_for(stopped.wait(), 1)
