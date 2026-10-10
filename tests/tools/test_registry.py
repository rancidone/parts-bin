
import pytest
from db.repository import SQLitePartsBinRepository

from domain import AddPartRequest, PartFields, PartsBinService
from tools import ApprovalReceipt, PartsBinToolRegistry, ToolExecutionContext


def _fields(**overrides):
    values = {"part_category": "resistor", "profile": "passive", "quantity": 2, "value": "10K", "package": "0402"}
    values.update(overrides)
    return PartFields(**values)


@pytest.fixture
def registry(tmp_path):
    return PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db")), approval_checker=lambda _name, _args: True)


@pytest.mark.asyncio
async def test_registry_is_schema_first_and_search_is_compact(registry):
    names = [tool["name"] for tool in registry.list_tools()]
    assert names == ["search_parts", "search_candidates", "get_part", "add_part", "add_stock", "update_part", "bulk_update_parts", "delete_part", "lookup_part_specs", "list_pending_reviews", "apply_review", "reject_review", "get_provenance", "get_specification_contract", "get_specifications", "stage_specification_review", "apply_specification_review", "reject_specification_review"]
    added = await registry.execute("add_part", {**vars(_fields()), "quantity": 2})
    assert added["ok"] is True
    found = await registry.execute("search_parts", {"filters": {"part_category": "resistor"}})
    assert found["result"]["parts"] == [{"id": 1, "part_category": "resistor", "profile": "passive", "value": "10k", "package": "0402", "part_number": None, "quantity": 2, "manufacturer": None, "description": None}]
    assert "created_at" not in found["result"]["parts"][0]


@pytest.mark.asyncio
async def test_registry_rejects_unknown_fields_and_requires_server_approval(registry):
    invalid = await registry.execute("get_part", {"part_id": 1, "sql": "select 1"})
    assert invalid == {"ok": False, "error": {"code": "invalid_input", "message": "Invalid arguments for get_part", "details": {"tool": "get_part"}}}
    part = registry.service.add_part(AddPartRequest(_fields()))
    denied = await PartsBinToolRegistry(registry.service).execute("delete_part", {"part_id": part.id})
    assert denied["error"]["code"] == "approval_required"
    approved = await registry.execute("delete_part", {"part_id": part.id}, context=ToolExecutionContext(ApprovalReceipt.issue("delete_part", {"part_id": part.id})))
    assert approved["result"]["deleted"] is True






@pytest.mark.asyncio
async def test_registry_completes_every_inventory_workflow(tmp_path):
    async def fetcher(_part_number):
        return {
            "chosen_updates": {"manufacturer": "Acme"},
            "durable_provenance": [{
                "field_name": "manufacturer", "field_value": "Acme",
                "source_tier": "fixture", "source_kind": "test",
                "extraction_method": "fixture",
            }],
            "provider": "fixture",
            "outcome": "match",
        }

    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"), spec_fetcher=fetcher)
    registry = PartsBinToolRegistry(service, approval_checker=lambda _name, _args: True)
    requests = []

    def call(name, arguments):
        requests.append((name, arguments))

    call("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 2, "value": "10K", "package": "0402"})
    call("get_part", {"part_id": 1})
    call("add_stock", {"part_id": 1, "quantity": 1})
    call("search_parts", {"filters": {"part_category": "resistor"}})
    call("update_part", {"part_id": 1, "fields": {"description": "updated"}})
    call("bulk_update_parts", {"part_ids": [1], "fields": {"package": "0603"}})
    call("get_provenance", {"part_id": 1})
    call("add_part", {"part_category": "transistor", "profile": "discrete_ic", "quantity": 1, "part_number": "2N7002"})
    call("lookup_part_specs", {"part_id": 2})
    call("list_pending_reviews", {})
    call("apply_review", {"part_id": 2})
    call("lookup_part_specs", {"part_id": 2})
    call("reject_review", {"part_id": 2, "fields": ["manufacturer"]})
    call("delete_part", {"part_id": 1})

    for name, arguments in requests:
        receipt = ApprovalReceipt.issue(name, arguments)
        response = await registry.execute(name, arguments, context=ToolExecutionContext(receipt))
        assert response["ok"], response
    assert [tool["name"] for tool in registry.list_tools()] == [
        "search_parts", "search_candidates", "get_part", "add_part", "add_stock", "update_part",
        "bulk_update_parts", "delete_part", "lookup_part_specs",
        "list_pending_reviews", "apply_review", "reject_review", "get_provenance",
        "get_specification_contract", "get_specifications", "stage_specification_review",
        "apply_specification_review", "reject_specification_review",
    ]




@pytest.mark.asyncio
async def test_add_ic_stages_specs_without_overwriting_inventory(tmp_path):
    from unittest.mock import AsyncMock
    from domain import GetPartRequest
    fetcher = AsyncMock(return_value={"status": "matched", "chosen_updates": {"description": "Dual operational amplifier"}})
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"), spec_fetcher=fetcher)
    registry = PartsBinToolRegistry(service)
    outcome = await registry.execute("add_part", {"part_category": "operational amplifier", "profile": "discrete_ic", "part_number": "NE5532", "quantity": 10, "package": "DIP"})
    assert outcome["ok"]
    fetcher.assert_awaited_once_with("NE5532")
    assert outcome["result"]["enrichment"]["status"] == "matched"
    assert service.get(GetPartRequest(outcome["result"]["id"])).description is None
    assert service.list_pending_reviews()


@pytest.mark.asyncio
async def test_supplier_failure_does_not_fail_successful_ic_add(tmp_path):
    from unittest.mock import AsyncMock
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"), spec_fetcher=AsyncMock(side_effect=RuntimeError("offline")))
    outcome = await PartsBinToolRegistry(service).execute("add_part", {"part_category": "operational amplifier", "profile": "discrete_ic", "part_number": "UA741CN", "quantity": 6})
    assert outcome["ok"]
    assert outcome["result"]["enrichment"]["status"] == "failed"
    assert len(service.list()) == 1
    assert service.list()[0].quantity == 6


async def test_approved_edit_is_found_by_equivalent_value_notation(registry):
    added = await registry.execute('add_part', {
        'part_category': 'resistor', 'profile': 'passive', 'quantity': 5,
        'value': '10K', 'package': '0402',
    })
    part_id = added['result']['id']
    arguments = {'part_id': part_id, 'fields': {'value': '5K1'}}
    edited = await registry.execute('update_part', arguments,
        context=ToolExecutionContext(ApprovalReceipt.issue('update_part', arguments)))
    assert edited['ok'] and edited['result']['value'] == '5.1k'
    found = await registry.execute('search_parts', {'filters': {
        'part_category': 'resistor', 'value': '5K1', 'package': '0402',
    }})
    assert found['result']['count'] == 1
    assert found['result']['parts'][0]['id'] == part_id
    assert found['result']['parts'][0]['quantity'] == 5


@pytest.mark.asyncio
async def test_search_nominal_value_and_stock_before_result_limit(registry):
    for value, package, quantity in [('10k', '0402', 3), ('10000r', '0603', 4), ('10k', '0805', 5)]:
        registry.service.add_part(AddPartRequest(_fields(value=value, package=package, quantity=quantity)))
    found = await registry.execute('search_parts', {
        'filters': {'part_category': 'resistor', 'value': '10 kΩ'},
        'minimum_quantity': 4, 'limit': 1,
    })
    assert found['ok']
    assert found['result']['count'] == 2
    assert found['result']['truncated'] is True
    assert [part['quantity'] for part in found['result']['parts']] == [4]
    invalid = await registry.execute('search_parts', {'minimum_quantity': True})
    assert invalid['error']['code'] == 'invalid_input'
    unsupported = await registry.execute('search_parts', {'filters': {'tolerance': '1%'}})
    assert unsupported['error']['code'] == 'invalid_input'


@pytest.mark.asyncio
async def test_specification_discovery_assertion_review_and_search(registry):
    discovery = await registry.execute('get_specification_contract', {'category': 'switch'})
    assert discovery['result']['fields']['rated_current']['unit'] == 'A'
    assert not (await registry.execute('get_specification_contract', {'category': 'connector'}))['result']['supported']
    part = registry.service.add_part(AddPartRequest(_fields()))
    proposed = {'name': 'tolerance', 'value': '1 %', 'basis': 'maximum', 'conditions': {},
                'evidence': {'kind': 'user_assertion', 'excerpt': 'These resistors are marked 1%.'}}
    assert (await registry.execute('stage_specification_review', {'part_id': part.id, 'facts': [proposed]}))['ok']
    assert (await registry.execute('apply_specification_review', {'part_id': part.id}))['error']['code'] == 'approval_required'
    args = {'part_id': part.id}
    assert (await registry.execute('apply_specification_review', args,
        context=ToolExecutionContext(ApprovalReceipt.issue('apply_specification_review', args))))['ok']
    query = {key: value for key, value in proposed.items() if key != 'evidence'} | {'comparison': 'lte'}
    result = await registry.execute('search_parts', {'filters': {'part_category': 'resistor'}, 'requirements': [query]})
    assert result['result']['matches'] == []  # Approved assertions are still assertions.
    assert result['result']['incomplete_count'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('fact', [
    {}, {'name': 'tolerance', 'value': '1 %', 'basis': 'maximum', 'conditions': {}, 'evidence': {}},
    {'name': 'tolerance', 'value': '1 %', 'basis': 'maximum', 'conditions': {}, 'evidence': {'kind': 'source', 'excerpt': 'invented source'}},
])
async def test_nested_fact_schema_rejects_invalid_or_model_invented_source(registry, fact):
    response = await registry.execute('stage_specification_review', {'part_id': 1, 'facts': [fact]})
    assert response['error']['code'] == 'invalid_input'
