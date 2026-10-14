"""HTTP adapter tests for the gateway and inventory surfaces."""

import pytest
from agent_runtime import ConversationEvent
from tests.storage import TestRepository
from tests.storage import TestConversations
from starlette.testclient import TestClient
from starlette.requests import Request
from application import ApplicationServices, create_services
import server


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "parts.db"
    store = TestConversations(db_path)
    repository = TestRepository(db_path)

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
    from tests.storage import connection, historical_thread
    http, path = client
    thread_id = http.post("/agent/threads").json()["thread_id"]
    store = http.app.state.services.gateway.store
    store.append(ConversationEvent("user_message", thread_id, "openai", {"text": "Find my op amps"}))
    with connection(path) as conn:
        historical_thread(path, "old-thread", "local")
        conn.execute("""INSERT INTO agent_events(thread_id, sequence, kind, runtime, data_json)
                        VALUES (%s, %s, %s, %s, %s)""",
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
    from tests.storage import connection, historical_thread
    with connection(client[1]) as conn:
        historical_thread(client[1], "old-thread", runtime)
        conn.execute("INSERT INTO agent_events VALUES (%s, %s, %s, %s, %s)",
                     ("old-thread", 1, "assistant_text", runtime, '{"text":"preserved history"}'))
    assert "preserved history" in client[0].get("/agent/threads/old-thread/events").text
    assert client[0].post("/agent/threads/old-thread/messages", data={"message": "continue", "execution_id": "test-request"}).status_code == 409
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
    store = runtime.registry.service.repository.conversations
    runtime.store = store
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
async def test_message_stream_disconnect_leaves_durable_delivery_running(tmp_path):
    import asyncio
    from tests.agent_runtime.test_dispatch import build, due
    from tests.agent_runtime.test_background_workers import Provider

    provider = Provider([{'status': 'completed', 'output': []}])
    services = build(tmp_path, provider)
    request = Request({'type': 'http', 'app': server.create_app(services)})
    thread = services.gateway.create_thread()
    response = await server.submit_agent_message(request, thread, 'hello', None, 'stable-request')
    assert response.headers['x-accel-buffering'] == 'no'
    chunk = await asyncio.wait_for(anext(response.body_iterator), 1)
    assert '"kind": "user_message"' in chunk
    await response.body_iterator.aclose()
    assert provider.posts == 0
    assert await services.dispatch.run_once()
    due(services.domain.repository)
    assert await services.dispatch.run_once()
    assert services.gateway.events(thread)[-1].data['status'] == 'completed'
    assert provider.posts == 1
    await services.close()


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


@pytest.mark.parametrize('initial_outcome', ['retrieval_failure', 'no_match', 'needs_clarification'])
def test_refresh_retries_electrical_extraction_with_supplier_replacement_url(client, initial_outcome):
    from unittest.mock import AsyncMock, call
    from domain import DomainError, ErrorCode, GetPartRequest
    http, _ = client
    service = http.app.state.services.domain
    part = opamp_for_refresh(service)
    replacement = 'https://www.ti.com/lit/ds/symlink/lm358b.pdf'
    facts = sourced_opamp_facts()
    facts[0]['evidence']['url'] = replacement
    first = (DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE, 'PDF retrieval failed')
             if initial_outcome == 'retrieval_failure' else {'outcome': initial_outcome, 'facts': []})
    service.datasheet_fetcher = AsyncMock(side_effect=[first, {'outcome': 'proposal', 'facts': facts}])
    service.spec_fetcher = AsyncMock(return_value={
        'outcome': 'saved', 'chosen_updates': {'datasheet_url': replacement}, 'durable_provenance': []})

    response = http.post(f'/inventory/{part.id}/refresh')

    assert response.status_code == 200
    assert response.json()['proposed_updates'] == {'datasheet_url': replacement}
    electrical = response.json()['electrical']
    assert electrical['review_staged'] and electrical['pending_review']['facts'] == facts
    assert electrical['facts'] == []
    assert service.get(GetPartRequest(part.id)) == part
    assert service.list_pending_reviews()[part.id]['fields']['datasheet_url']['value'] == replacement
    service.spec_fetcher.assert_awaited_once_with(part.part_number)
    assert service.datasheet_fetcher.await_args_list == [call(part, part.datasheet_url), call(part, replacement)]


@pytest.mark.parametrize('replacement', [None, 'https://www.ti.com/lit/ds/symlink/lm358.pdf'])
def test_refresh_does_not_retry_electrical_extraction_without_a_new_supplier_url(client, replacement):
    from unittest.mock import AsyncMock
    http, _ = client
    service = http.app.state.services.domain
    part = opamp_for_refresh(service)
    service.datasheet_fetcher = AsyncMock(return_value={'outcome': 'no_match', 'facts': []})
    service.spec_fetcher = AsyncMock(return_value={
        'outcome': 'saved' if replacement else 'no_match',
        'chosen_updates': {'datasheet_url': replacement} if replacement else {}, 'durable_provenance': []})

    response = http.post(f'/inventory/{part.id}/refresh')

    assert response.status_code == 200
    assert response.json()['electrical']['outcome'] == 'no_match'
    service.datasheet_fetcher.assert_awaited_once_with(part, part.datasheet_url)
    service.spec_fetcher.assert_awaited_once_with(part.part_number)


def test_refresh_reports_replacement_failure_without_retrying_again(client):
    from unittest.mock import AsyncMock, call
    from domain import DomainError, ErrorCode, GetPartRequest
    http, _ = client
    service = http.app.state.services.domain
    part = opamp_for_refresh(service)
    replacement = 'https://www.ti.com/lit/ds/symlink/lm358b.pdf'
    service.datasheet_fetcher = AsyncMock(side_effect=[
        {'outcome': 'no_match', 'facts': []},
        DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE, 'Replacement PDF retrieval failed')])
    service.spec_fetcher = AsyncMock(return_value={
        'outcome': 'saved', 'chosen_updates': {'datasheet_url': replacement}, 'durable_provenance': []})

    response = http.post(f'/inventory/{part.id}/refresh')

    assert response.status_code == 200
    electrical = response.json()['electrical']
    assert electrical['outcome'] == 'retrieval_failure'
    assert electrical['clarification'] == 'Replacement PDF retrieval failed'
    assert electrical['pending_review'] is None and electrical['facts'] == []
    assert service.get(GetPartRequest(part.id)) == part
    service.spec_fetcher.assert_awaited_once_with(part.part_number)
    assert service.datasheet_fetcher.await_args_list == [call(part, part.datasheet_url), call(part, replacement)]


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


def bulk_parts(http):
    from domain import AddPartRequest, PartFields
    service = http.app.state.services.domain
    return [service.add_part(AddPartRequest(PartFields(
        part_category='resistor', profile='passive', quantity=quantity, value=value,
        package='0402', description='old')))
        for value, quantity in [('10k', 3), ('22k', 7)]]


def test_bulk_review_is_read_only_then_applies_once(client):
    http, _ = client
    parts = bulk_parts(http)
    service = http.app.state.services.domain
    before = service.list()
    service.repository.inventory.save_pending_review(parts[0].id, {'description': 'pending'}, [])
    payload = {'part_ids': [part.id for part in parts], 'fields': {
        'package': ' 0603 ', 'description': None, 'datasheet_url': 'https://example.com/datasheet.pdf'}}
    response = http.post('/inventory/bulk/preview', json=payload)
    assert response.status_code == 200
    review = response.json()
    assert service.list() == before
    assert review['cleared_reviews'] == [parts[0].id]
    assert [row['after']['package'] for row in review['changes']] == ['0603', '0603']
    assert [row['after']['quantity'] for row in review['changes']] == [3, 7]
    decision = {'request_id': review['request_id'], 'approved': True}
    result = http.post('/inventory/bulk/decide', json=decision)
    assert result.status_code == 200
    assert all(part['datasheet_url'] == 'https://example.com/datasheet.pdf' for part in result.json()['parts'])
    saved = service.list()
    assert [part.quantity for part in saved] == [3, 7]
    assert all(part.package == '0603' and part.description is None for part in saved)
    assert service.list_pending_reviews() == {}
    assert http.post('/inventory/bulk/decide', json=decision).json() == result.json()
    assert service.list() == saved


def test_bulk_decline_is_final_and_changed_stock_only_blocks_its_part(client):
    from domain import AdjustStockRequest, GetPartRequest
    http, _ = client
    parts = bulk_parts(http)
    service = http.app.state.services.domain
    payload = {'part_ids': [part.id for part in parts], 'fields': {'quantity': 12}}
    review = http.post('/inventory/bulk/preview', json=payload).json()
    before = service.list()
    assert http.post('/inventory/bulk/decide', json={'request_id': review['request_id'], 'approved': False}).status_code == 200
    assert http.post('/inventory/bulk/decide', json={'request_id': review['request_id'], 'approved': True}).status_code == 409
    assert service.list() == before
    review = http.post('/inventory/bulk/preview', json=payload).json()
    service.adjust_stock(AdjustStockRequest(parts[1].id, 1))
    changed = service.list()
    response = http.post('/inventory/bulk/decide', json={'request_id': review['request_id'], 'approved': True})
    assert response.status_code == 200
    assert response.json()['errors'][0]['part_id'] == parts[1].id
    assert service.get(GetPartRequest(parts[0].id)).quantity == 12
    assert service.get(GetPartRequest(parts[1].id)).quantity == changed[1].quantity


@pytest.mark.parametrize('fields', [{'quantity': -1}, {'id': 9}, {}])
def test_bulk_invalid_batch_has_no_partial_writes(client, fields):
    http, _ = client
    parts = bulk_parts(http)
    service = http.app.state.services.domain
    before = service.list()
    response = http.post('/inventory/bulk/preview', json={'part_ids': [p.id for p in parts], 'fields': fields})
    assert response.status_code in (409, 422)
    assert service.list() == before


@pytest.mark.parametrize('ids', [[], [1, 1], [True], ['1'], list(range(1, 102)), [[1]], [999]])
def test_bulk_invalid_selection(client, ids):
    http, _ = client
    bulk_parts(http)
    assert http.post('/inventory/bulk/preview', json={'part_ids': ids, 'fields': {'description': 'test'}}).status_code in (404, 422)


def test_bulk_decision_is_bound_to_server_review(client):
    http, _ = client
    assert http.post('/inventory/bulk/decide', json={'request_id': 'missing', 'approved': True}).status_code == 404
    assert http.post('/inventory/bulk/decide', json={'request_id': 'missing', 'approved': 1}).status_code == 422
    parts = bulk_parts(http)
    review = http.post('/inventory/bulk/preview', json={'part_ids': [parts[0].id], 'fields': {'quantity': 0}}).json()
    assert http.post('/inventory/bulk/decide', json={'request_id': review['request_id'], 'approved': True, 'fields': {'quantity': 99}}).status_code == 422


def test_electrical_search_contracts_and_validation(client):
    http, _ = client
    contracts = http.get('/inventory/specification-contracts')
    assert contracts.status_code == 200
    resistor = next(item for item in contracts.json()['categories'] if item['category'] == 'resistor')
    assert resistor['fields']['resistance']['unit'] == 'Ω'
    requirement = {'name': 'resistance', 'comparison': 'eq', 'value': '10 kΩ',
                   'basis': 'nominal', 'conditions': {}}
    query = {'filters': {'part_category': 'resistor'}, 'requirements': [requirement]}
    response = http.post('/inventory/specification-search', json=query)
    assert response.status_code == 200
    assert response.json() == {'matches': [], 'incomplete': [], 'partial_matches': [], 'partial_match_count': 0, 'match_count': 0,
                               'incomplete_count': 0, 'truncated': False, 'next_offset': None}
    for change in [{'offset': -1}, {'limit': 101}, {'minimum_quantity': True},
                   {'requirements': []}, {'filters': {}}, {'unexpected': 1},
                   {'requirements': [{**requirement, 'basis': 'absolute_maximum'}]}]:
        assert http.post('/inventory/specification-search', json={**query, **change}).status_code == 422


def test_electrical_search_separates_accepted_pending_and_asserted_evidence(client):
    from domain import AddPartRequest, PartFields, GetPartRequest
    http, _ = client
    service = http.app.state.services.domain
    parts = [service.add_part(AddPartRequest(PartFields(
        'operational amplifier', 'discrete_ic', 4, part_number=f'LM358BIDR-{i}', package='SOIC-8')))
             for i in range(4)]
    facts = sourced_opamp_facts()
    facts[0]['evidence']['part_number'] = parts[0].part_number
    service.stage_specifications(parts[0], facts)
    service.apply_specification_review(parts[0].id)
    service.stage_specifications(parts[1], [{**facts[0], 'evidence': {**facts[0]['evidence'], 'part_number': parts[1].part_number}}])
    asserted = [{**facts[0], 'evidence': {'kind': 'user_assertion', 'excerpt': 'I think supply is 3 V'}}]
    service.stage_specifications(parts[2], asserted)
    service.apply_specification_review(parts[2].id)
    service.stage_specifications(parts[3], [{**facts[0], 'value': '5 V', 'evidence': {**facts[0]['evidence'], 'part_number': parts[3].part_number}}])
    service.apply_specification_review(parts[3].id)
    before_search = service.list()
    query = {'filters': {'part_category': 'opamp'}, 'requirements': [{
        **{key: facts[0][key] for key in ('name', 'value', 'basis', 'conditions')},
        'comparison': 'lte'}], 'limit': 1}
    first = http.post('/inventory/specification-search', json=query).json()
    assert first['match_count'] == 1 and first['incomplete_count'] == 2
    assert first['partial_match_count'] == 1
    assert first['partial_matches'][0]['match_kind'] == 'partial'
    assert first['partial_matches'][0]['failed_requirements'][0]['fact']['value'] == '5 V'
    assert first['partial_matches'][0]['failed_requirements'][0]['requirement']['value'] == '3 V'
    assert first['matches'][0]['part']['id'] == parts[0].id
    assert first['matches'][0]['supporting_facts'] == facts
    assert first['incomplete'][0]['part']['id'] == parts[1].id
    assert first['next_offset'] == 1
    second = http.post('/inventory/specification-search', json={**query, 'offset': 1}).json()
    assert second['matches'] == []
    assert second['incomplete'][0]['part']['id'] == parts[2].id
    assert second['next_offset'] is None
    assert service.list() == before_search
    assert all(service.get(GetPartRequest(part.id)).quantity == 4 for part in parts)
    assert service.get_specifications(parts[1].id)['pending_review'] is not None


def test_message_intake_requires_stable_identity(client):
    thread = client[0].post('/agent/threads').json()['thread_id']
    assert client[0].post(f'/agent/threads/{thread}/messages', data={'message': 'hello'}).status_code == 422
