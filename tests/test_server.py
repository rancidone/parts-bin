"""HTTP adapter tests for the gateway and inventory surfaces."""

import pytest
from agent_runtime import ConversationEvent
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


def test_agent_threads_lists_stored_conversations_without_starting_a_provider(client):
    import sqlite3
    http, path = client
    thread_id = http.post("/agent/threads").json()["thread_id"]
    store = http.app.state.services.gateway.store
    store.append(ConversationEvent("user_message", thread_id, "openai", {"text": "Find my op amps"}))
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO agent_threads(thread_id, runtime) VALUES (?, ?)", ("old-thread", "local"))
        conn.execute("""INSERT INTO agent_events(thread_id, sequence, kind, runtime, data_json)
                        VALUES (?, ?, ?, ?, ?)""",
                     ("old-thread", 1, "assistant_text", "local", '{"text":"Preserved local history"}'))

    response = http.get("/agent/threads")
    assert response.status_code == 200
    assert response.json() == {"threads": [
        {"thread_id": "old-thread", "runtime": "local", "title": "Preserved local history", "last_sequence": 1},
        {"thread_id": thread_id, "runtime": "openai", "title": "Find my op amps", "last_sequence": 1},
    ]}


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


def test_refresh_exposes_supplier_candidates_without_changing_stock_or_existing_review(client):
    from unittest.mock import AsyncMock
    from domain import AddPartRequest, PartFields
    from tools import PartsBinToolRegistry
    import asyncio

    http, _ = client
    service = http.app.state.services.domain
    part = service.add_part(AddPartRequest(PartFields(
        part_category="audio amplifier", profile="discrete_ic", quantity=3, part_number="LM386")))
    service.repository.inventory.save_pending_review(part.id, {"description": "existing review"}, [])
    candidates = [{"part_number": "LM386N-1/NOPB", "manufacturer": "Texas Instruments",
                   "source_locator": "https://www.digikey.com/example"}]
    service.spec_fetcher = AsyncMock(return_value={
        "outcome": "needs_clarification", "chosen_updates": {}, "durable_provenance": [],
        "lookup_candidates": candidates, "candidate_count": 34,
    })
    before = service.list()
    reviews = service.list_pending_reviews()
    response = http.post(f"/inventory/{part.id}/refresh")
    assert response.status_code == 200
    assert response.json()["outcome"] == "needs_clarification"
    assert response.json()["lookup_candidates"] == candidates
    assert response.json()["candidate_count"] == 34
    result = asyncio.run(PartsBinToolRegistry(service).execute("lookup_part_specs", {"part_id": part.id}))
    assert result["result"]["lookup_candidates"] == candidates
    assert result["result"]["candidate_count"] == 34
    assert service.list() == before
    assert service.list_pending_reviews() == reviews


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


def test_quantity_adjustments_use_current_stock_and_preserve_reviews(client):
    from domain import AddPartRequest, PartFields

    http, _ = client
    service = http.app.state.services.domain
    part = service.add_part(AddPartRequest(PartFields(part_category='resistor', profile='passive', quantity=1, value='10k')))
    service.repository.inventory.save_pending_review(part.id, {'description': 'pending'}, [])
    reviews = http.get('/inventory/pending').json()
    url = f'/inventory/{part.id}/quantity'
    assert http.post(url, json={'delta': -1}).json()['part']['quantity'] == 0
    assert http.post(url, json={'delta': -1}).status_code == 422
    assert http.post(url, json={'delta': 1}).json()['part']['quantity'] == 1
    assert http.post(url, json={'delta': 1}).json()['part']['quantity'] == 2
    assert http.get('/inventory/pending').json() == reviews
    assert http.post('/inventory/999/quantity', json={'delta': 1}).status_code == 404


@pytest.mark.parametrize('body', [{}, {'delta': True}, {'delta': 1.0}, {'delta': '1'}, {'delta': 0}, {'delta': 2}, {'delta': -2}, {'delta': 1, 'quantity': 10}])
def test_quantity_adjustments_reject_invalid_requests(client, body):
    assert client[0].post('/inventory/1/quantity', json=body).status_code == 422


def opamp_for_refresh(service):
    from domain import AddPartRequest, PartFields
    return service.add_part(AddPartRequest(PartFields(
        'operational amplifier', 'discrete_ic', 4, part_number='LM358BIDR',
        datasheet_url='https://www.ti.com/lit/ds/symlink/lm358.pdf')))


def sourced_opamp_facts():
    return [{'name': 'minimum_supply_voltage', 'value': '3 V', 'basis': 'operating_minimum',
             'conditions': {'ambient_temperature': '-40 to 85 °C', 'supply_convention': 'total rail-to-rail'},
             'evidence': {'kind': 'source', 'url': 'https://www.ti.com/lit/ds/symlink/lm358.pdf',
                          'page': 5, 'excerpt': 'LM358B LM358BA 3 36 V', 'sha256': 'a' * 64,
                          'retrieved_at': '2026-10-10T00:00:00Z', 'part_number': 'LM358BIDR'}}]


def test_ui_refresh_stages_opamp_electrical_facts_and_requires_displayed_review(client):
    from unittest.mock import AsyncMock
    from domain import GetPartRequest
    http, _ = client
    service = http.app.state.services.domain
    part = opamp_for_refresh(service)
    service.spec_fetcher = AsyncMock()
    service.datasheet_fetcher = AsyncMock(return_value={'outcome': 'proposal', 'facts': sourced_opamp_facts()})
    refreshed = http.post(f'/inventory/{part.id}/refresh')
    assert refreshed.status_code == 200
    electrical = refreshed.json()['electrical']
    assert electrical['review_staged'] and electrical['facts'] == []
    service.spec_fetcher.assert_not_awaited()
    service.datasheet_fetcher.assert_awaited_once_with(part, part.datasheet_url)
    read = http.get(f'/inventory/{part.id}/specifications')
    assert read.json()['pending_review'] == electrical['pending_review']
    rejected = http.post(f'/inventory/{part.id}/specifications/decide', json={'review': {}, 'approved': True})
    assert rejected.status_code == 409
    assert service.get_specifications(part.id)['facts'] == []
    accepted = http.post(f'/inventory/{part.id}/specifications/decide', json={
        'review': electrical['pending_review'], 'approved': True})
    assert accepted.status_code == 200
    assert accepted.json()['facts'] == sourced_opamp_facts()
    assert accepted.json()['pending_review'] is None
    assert service.get(GetPartRequest(part.id)).quantity == 4
    repeated = http.post(f'/inventory/{part.id}/specifications/decide', json={
        'review': electrical['pending_review'], 'approved': True})
    assert repeated.status_code == 409
    assert service.get_specifications(part.id)['facts'] == sourced_opamp_facts()


def test_ui_electrical_review_survives_refresh_and_dismissal_keeps_accepted_facts(client):
    from unittest.mock import AsyncMock
    http, _ = client
    service = http.app.state.services.domain
    part = opamp_for_refresh(service)
    service.stage_specifications(part, sourced_opamp_facts())
    service.datasheet_fetcher = AsyncMock()
    refresh = http.post(f'/inventory/{part.id}/refresh').json()
    assert refresh['electrical']['outcome'] == 'pending_review'
    service.datasheet_fetcher.assert_not_awaited()
    dismissed = http.post(f'/inventory/{part.id}/specifications/decide', json={
        'review': refresh['electrical']['pending_review'], 'approved': False})
    assert dismissed.status_code == 200
    assert dismissed.json()['pending_review'] is None and dismissed.json()['facts'] == []


def test_ui_refresh_distinguishes_source_failure_without_changing_stock(client):
    from unittest.mock import AsyncMock
    from domain import DomainError, ErrorCode, GetPartRequest
    http, _ = client
    service = http.app.state.services.domain
    part = opamp_for_refresh(service)
    service.datasheet_fetcher = AsyncMock(side_effect=DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE, 'PDF retrieval failed'))
    response = http.post(f'/inventory/{part.id}/refresh')
    assert response.status_code == 200
    assert response.json()['electrical']['outcome'] == 'retrieval_failure'
    assert response.json()['electrical']['pending_review'] is None
    assert service.get(GetPartRequest(part.id)) == part


@pytest.mark.parametrize('category', ['resistor', 'connector', 'module', 'unmarked stock'])
def test_all_categories_can_save_and_clear_datasheet_links(client, category):
    from domain import AddPartRequest, GetPartRequest, PartFields
    http, _ = client
    service = http.app.state.services.domain
    part = service.add_part(AddPartRequest(PartFields(category, 'discrete_ic', 2)))
    saved = http.patch(f'/inventory/{part.id}', json={'part': {'datasheet_url': 'https://example.com/source.pdf'}})
    assert saved.status_code == 200
    assert saved.json()['part']['datasheet_url'] == 'https://example.com/source.pdf'
    assert service.get(GetPartRequest(part.id)).quantity == 2
    cleared = http.patch(f'/inventory/{part.id}', json={'part': {'datasheet_url': None}})
    assert cleared.status_code == 200 and cleared.json()['part']['datasheet_url'] is None


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://example.com/a.pdf', 'https://user:password@example.com/a.pdf', 'https://example.com/ bad', 'https://example.com/' + 'x' * 2048])
def test_inventory_rejects_invalid_datasheet_link(client, url):
    http, _ = client
    part = opamp_for_refresh(http.app.state.services.domain)
    assert http.patch(f'/inventory/{part.id}', json={'part': {'datasheet_url': url}}).status_code == 422


def test_supplier_datasheet_discovery_stages_link_and_electrical_review_separately(client):
    from unittest.mock import AsyncMock
    from domain import GetPartRequest, UpdatePartRequest
    http, _ = client
    service = http.app.state.services.domain
    part = opamp_for_refresh(service)
    part = service.update_part(UpdatePartRequest(part.id, {'datasheet_url': None}))
    link = 'https://www.ti.com/lit/ds/symlink/lm358.pdf'
    service.spec_fetcher = AsyncMock(return_value={'outcome': 'saved', 'chosen_updates': {'datasheet_url': link},
                                                 'durable_provenance': []})
    service.datasheet_fetcher = AsyncMock(return_value={'outcome': 'proposal', 'facts': sourced_opamp_facts()})
    response = http.post(f'/inventory/{part.id}/refresh')
    assert response.status_code == 200
    assert response.json()['proposed_updates'] == {'datasheet_url': link}
    assert response.json()['electrical']['review_staged']
    assert service.list_pending_reviews()[part.id]['fields']['datasheet_url']['value'] == link
    assert service.get(GetPartRequest(part.id)) == part
    service.datasheet_fetcher.assert_awaited_once_with(part, link)


def test_datasheet_link_on_unsupported_category_remains_available_without_extraction(client):
    from unittest.mock import AsyncMock
    from domain import AddPartRequest, GetPartRequest, PartFields
    http, _ = client
    service = http.app.state.services.domain
    part = service.add_part(AddPartRequest(PartFields('connector', 'discrete_ic', 2,
        datasheet_url='https://example.com/connector.pdf')))
    service.datasheet_fetcher = AsyncMock()
    response = http.post(f'/inventory/{part.id}/refresh')
    assert response.status_code == 200
    assert response.json()['electrical']['outcome'] == 'unsupported'
    assert service.get(GetPartRequest(part.id)).datasheet_url == part.datasheet_url
    service.datasheet_fetcher.assert_not_awaited()
