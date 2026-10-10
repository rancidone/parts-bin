"""HTTP adapter tests for the gateway and inventory surfaces."""

import pytest
from db.repository import SQLitePartsBinRepository
from db.conversations import SQLiteConversationRepository
from starlette.testclient import TestClient
from starlette.requests import Request
from application import ApplicationServices, create_services
import server


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "parts.db"
    store = SQLiteConversationRepository(db_path)
    repository = SQLitePartsBinRepository(db_path)

    def unavailable():
        raise RuntimeError("OpenAI is not configured")

    services = create_services(repository, store, transport_factory=unavailable, agent_configured=False)
    with TestClient(server.create_app(services), raise_server_exceptions=True) as test_client:
        yield test_client, db_path


def test_health_reports_agent_configuration(client):
    response = client[0].get("/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "agent_configured"}


def test_agent_thread_uses_openai_without_a_picker(client):
    response = client[0].post("/agent/threads")
    assert response.status_code == 200
    thread_id = response.json()["thread_id"]
    assert client[0].app.state.services.gateway.store.runtime_for(thread_id) == "openai"


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
    assert client[0].post("/agent/threads/old-thread/resume", data={"execution_id": "old"}).status_code == 409
    assert client[0].app.state.services.gateway.store.runtime_for("old-thread") == runtime


def test_inventory_still_opens(client):
    response = client[0].get("/inventory")
    assert response.status_code == 200
    assert response.json() == []


def test_resume_endpoint_checks_execution_identity(client):
    thread = client[0].post("/agent/threads").json()["thread_id"]
    assert client[0].post(f"/agent/threads/{thread}/resume", data={"execution_id": " "}).status_code == 422
    assert client[0].post("/agent/threads/missing/resume", data={"execution_id": "missing"}).status_code == 404


@pytest.mark.asyncio
async def test_resume_endpoint_replays_completed_execution_without_model_call(tmp_path):
    from agent_runtime import AgentGateway, ModelTurn
    from tests.agent_runtime.test_runtime import build_runtime

    runtime, transport, store = build_runtime(tmp_path, [ModelTurn("done")])
    gateway = AgentGateway(store, lambda: runtime)
    app = server.create_app(ApplicationServices(runtime.registry.service, gateway, True))
    request = Request({"type": "http", "app": app})
    thread = gateway.create_thread()
    events = await gateway.submit(thread, "hello")
    response = await server.resume_agent_execution(request, thread, events[0].data["execution_id"], None)
    chunks = [chunk async for chunk in response.body_iterator]
    assert len(chunks) == 3
    assert '"status": "completed"' in chunks[-1]
    assert len(transport.requests) == 1
    assert gateway.events(thread) == events


@pytest.mark.asyncio
async def test_message_endpoint_returns_sse_before_turn_finishes(tmp_path):
    import asyncio
    from agent_runtime import ModelTurn, ToolCall
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

    store = SQLiteConversationRepository(tmp_path / "conversation.db")
    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    services = create_services(repository, store, transport_factory=PausedTransport)
    gateway = services.gateway
    app = server.create_app(services)
    request = Request({"type": "http", "app": app})
    thread = gateway.create_thread()
    response = await asyncio.wait_for(server.submit_agent_message(request, thread, "search", None), 1)
    assert response.headers["x-accel-buffering"] == "no"
    for kind in ["user_message", "tool_call", "tool_result"]:
        chunk = await asyncio.wait_for(anext(response.body_iterator), 1)
        assert f'"kind": "{kind}"' in chunk
    assert not release.is_set()
    # Disconnecting closes the producer, including a still-pending model call.
    await response.body_iterator.aclose()
    await asyncio.wait_for(stopped.wait(), 1)


def test_edit_normalizes_value_and_agent_search_finds_it(client):
    from domain import AddPartRequest, PartFields, SearchPartsRequest
    http, _path = client
    service = http.app.state.services.domain
    added = service.add_part(AddPartRequest(PartFields(
        'resistor', 'passive', 5, value='10K', package='0603')))
    edited = http.patch(f'/inventory/{added.id}', json={'part': {'value': '22K'}})
    assert edited.status_code == 200
    assert edited.json()['part']['value'] == '22k'
    found = service.search(SearchPartsRequest({'part_category': 'resistor', 'value': '22K'}))
    assert [part.id for part in found] == [added.id]
