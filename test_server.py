"""HTTP adapter tests for the gateway and inventory surfaces."""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import server
from db.persistence import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "parts.db"
    init_db(db_path)
    with patch.object(server, "_DB_PATH", db_path):
        with TestClient(server.app, raise_server_exceptions=True) as test_client:
            yield test_client, db_path


def test_health_reports_explicit_runtimes(client):
    response = client[0].get("/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "runtimes"}


@pytest.mark.parametrize("runtime", ["codex", "openai", "local"])
def test_agent_thread_selects_runtime(client, runtime):
    response = client[0].post("/agent/threads", json={"runtime": runtime})
    assert response.status_code == 200
    assert response.json()["runtime"] == runtime


def test_invalid_agent_thread_runtime_is_rejected(client):
    response = client[0].post("/agent/threads", json={"runtime": "unsupported"})
    assert response.status_code == 422


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
    gateway = AgentGateway(store, lambda _: OpenAIResponsesRuntime(PausedTransport(),
        registry=PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db")), store=store, approvals=ApprovalEngine()))
    monkeypatch.setattr(server, "_agent_gateway", gateway)
    thread = gateway.create_thread("openai")
    response = await asyncio.wait_for(server.submit_agent_message(thread, "search", None), 1)
    assert response.headers["x-accel-buffering"] == "no"
    for kind in ["user_message", "tool_call", "tool_result"]:
        chunk = await asyncio.wait_for(anext(response.body_iterator), 1)
        assert f'"kind": "{kind}"' in chunk
    assert not release.is_set()
    # Disconnecting closes the producer, including a still-pending model call.
    await response.body_iterator.aclose()
    await asyncio.wait_for(stopped.wait(), 1)
