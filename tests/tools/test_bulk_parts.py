"""Whole-list operations, rollback and durable approvals for mixed inventory edits."""
from unittest.mock import patch

import pytest

from agent_runtime import ApprovalEngine, ApprovalResponse, ModelTurn, OpenAIResponsesRuntime, ToolCall
from domain import GetPartRequest, PartsBinService, UpdatePartRequest
from tests.storage import TestRepository
from tests.agent_runtime.test_runtime import ScriptedTransport
from tools import PartsBinToolRegistry, ToolExecutionContext


VALUES = ['1r', '10r', '100r', '1k', '10k', '100k', '1m', '10m', '120r', '1k2']


def items():
    return [dict(part_category='resistor', profile='passive', value=value, package='0603', quantity=20,
                 description='1% per user') for value in VALUES]


@pytest.fixture
def registry(tmp_path):
    return PartsBinToolRegistry(PartsBinService(TestRepository(tmp_path / 'bulk')))


async def test_ten_resistors_are_added_in_one_call_and_replayed_once(registry):
    context = ToolExecutionContext(operation_id='bulk-add')
    result = await registry.execute('add_parts', {'items': items()}, context=context)
    assert result['ok'] and result['result']['count'] == 10
    assert len(registry.service.list()) == 10
    assert [part['quantity'] for part in result['result']['parts']] == [20] * 10
    assert await registry.execute('add_parts', {'items': items()}, context=context) == result
    assert len(registry.service.list()) == 10
    changed = await registry.execute('add_parts', {'items': items()[:1]}, context=context)
    assert changed['error']['code'] == 'conflict'


async def test_add_batch_rolls_back_every_row_on_invalid_input(registry):
    batch = items()
    batch[-1]['quantity'] = -1
    assert not (await registry.execute('add_parts', {'items': batch}))['ok']
    assert registry.service.list() == []


async def test_bulk_add_skips_normalized_existing_and_repeated_rows_without_changing_stock(registry):
    existing = (await registry.execute('add_part', {**items()[3], 'quantity': 0}))['result']
    before = registry.service.get(GetPartRequest(existing['id']))
    batch = [items()[3], {**items()[3], 'value': '1000 Ω', 'quantity': 99, 'description': 'replacement'},
             items()[0], {**items()[0], 'value': '1 Ω', 'quantity': 50}]
    context = ToolExecutionContext(operation_id='dedupe')
    result = await registry.execute('add_parts', {'items': batch}, context=context)
    assert result['ok']
    report = result['result']
    assert report['count'] == 1 and report['skipped_count'] == 3
    assert [row['status'] for row in report['items']] == ['skipped', 'skipped', 'added', 'skipped']
    assert [row['reason'] for row in report['items']] == ['existing', 'duplicate_input', None, 'duplicate_input']
    assert [row['duplicate_of'] for row in report['items']] == [None, 0, None, 2]
    assert [row['index'] for row in report['items']] == list(range(4))
    assert registry.service.get(GetPartRequest(existing['id'])) == before
    assert report['parts'][0]['quantity'] == 20
    assert await registry.execute('add_parts', {'items': batch}, context=context) == result
    again = await registry.execute('add_parts', {'items': batch})
    assert again['result']['count'] == 0 and again['result']['skipped_count'] == 4
    assert len(registry.service.list()) == 2


async def test_bulk_add_keeps_distinct_packages_suffixes_and_unidentified_groups(registry):
    batch = [items()[3], {**items()[3], 'package': '0805'},
             {'part_category': 'bjt', 'profile': 'discrete_ic', 'quantity': 3, 'part_number': 'PBSS5350T,215'},
             {'part_category': 'bjt', 'profile': 'discrete_ic', 'quantity': 4, 'part_number': 'PBSS5350T,115'},
             {'part_category': 'connector', 'profile': 'discrete_ic', 'quantity': 5, 'description': 'unmarked connector'}]
    batch.append(batch[-1].copy())
    result = await registry.execute('add_parts', {'items': batch})
    assert result['ok'] and result['result']['count'] == 6
    assert result['result']['skipped_count'] == 0


async def test_bulk_add_rolls_back_insertions_when_later_identity_check_fails(registry):
    from domain import DomainError, ErrorCode
    duplicate = PartsBinService.duplicate_for_add
    def check(self, fields):
        if fields['value'] == '10':
            raise DomainError(ErrorCode.CONFLICT, 'Ambiguous stored identity')
        return duplicate(self, fields)
    # Bound transaction services inherit the same domain rule, rather than a tool-only check.
    with patch.object(PartsBinService, 'duplicate_for_add', check):
        result = await registry.execute('add_parts', {'items': items()[:2]})
    assert result['error']['code'] == 'conflict'
    assert registry.service.list() == []


async def test_stock_batch_is_atomic_and_idempotent(registry):
    await registry.execute('add_parts', {'items': items()})
    args = {'items': [{'part_id': 1, 'quantity': 3}, {'part_id': 2, 'quantity': 7}]}
    context = ToolExecutionContext(operation_id='stock-batch')
    result = await registry.execute('add_stock_batch', args, context=context)
    assert [part['quantity'] for part in result['result']['parts']] == [23, 27]
    assert await registry.execute('add_stock_batch', args, context=context) == result
    before = registry.service.list()
    for invalid in ([{'part_id': 1, 'quantity': 1}, {'part_id': 999, 'quantity': 1}],
                    [{'part_id': 1, 'quantity': 1}, {'part_id': 1, 'quantity': 2}]):
        assert not (await registry.execute('add_stock_batch', {'items': invalid}))['ok']
        assert registry.service.list() == before


async def test_updates_have_one_approval_with_distinct_fields_and_replay(registry):
    await registry.execute('add_parts', {'items': items()})
    args = {'items': [{'part_id': 1, 'fields': {'quantity': 5}},
                      {'part_id': 2, 'fields': {'description': 'verified user label', 'quantity': 9}}]}
    assert (await registry.execute('update_parts', args))['error']['code'] == 'approval_required'
    engine = ApprovalEngine(registry.service.repository)
    pending = engine.request('thread', 'update_parts', args, service=registry.service)
    approved = engine.decide('thread', pending.request_id, True)
    result = await engine.execute(approved, registry)
    assert result['ok']
    assert [row['quantity'] for row in result['result']['parts']] == [5, 9]
    registry.service.update_part(UpdatePartRequest(1, {'quantity': 7}))
    assert await engine.execute(approved, registry) == result
    assert registry.service.get(GetPartRequest(1)).quantity == 7


@pytest.mark.parametrize('stale', [False, True])
async def test_failed_update_rolls_back_entire_batch(registry, stale):
    await registry.execute('add_parts', {'items': items()})
    args = {'items': [{'part_id': 1, 'fields': {'quantity': 5}},
                      {'part_id': 2, 'fields': {'value': '1r' if not stale else '10r'}}]}
    engine = ApprovalEngine(registry.service.repository)
    pending = engine.request('thread', 'update_parts', args, service=registry.service)
    if stale:
        registry.service.update_part(UpdatePartRequest(2, {'quantity': 99}))
    before = registry.service.list()
    result = await engine.execute(engine.decide('thread', pending.request_id, True), registry)
    assert result['error']['code'] == 'conflict'
    assert registry.service.list() == before


async def test_nested_and_or_search_is_one_inventory_read_with_exact_pagination(registry):
    await registry.execute('add_parts', {'items': items()})
    args = {'filters': {'part_category': 'resistor'}, 'expression': {'all': [
        {'filters': {'package': '0603'}},
        {'any': [{'filters': {'value': '1000 Ω'}}, {'filters': {'value': '1k'}},
                 {'all': [{'filters': {'value': '120r'}}, {'filters': {'profile': 'passive'}}]}]},
    ]}, 'limit': 1}
    with patch.object(registry.service.repository.inventory, 'search', wraps=registry.service.repository.inventory.search) as read:
        result = await registry.execute('search_parts', args)
    assert read.call_count == 1
    page = result['result']
    assert page['count'] == 2 and page['partial_match_count'] == 0
    assert page['next_offset'] == 1
    final = await registry.execute('search_parts', {**args, 'offset': 1})
    assert final['result']['next_offset'] is None
    assert {page['parts'][0]['value'], final['result']['parts'][0]['value']} == {'1000', '120'}


@pytest.mark.parametrize('expression', [
    {}, {'all': []}, {'any': []}, {'filters': {}}, {'filters': {'quantity': 2}},
    {'filters': {'value': '1r'}, 'any': [{'filters': {'value': '10r'}}]},
    {'any': [{'filters': {'value': '1r'}}, {'filters': {'unsupported': 'x'}}]},
    {'any': [{'filters': {'value': '1r'}}] * 100},
    {'all': [{'all': [{'all': [{'all': [{'all': [{'filters': {'value': '1r'}}]}]}]}]}]},
])
async def test_invalid_expression_is_rejected_even_in_unused_branches(registry, expression):
    result = await registry.execute('search_parts', {'expression': expression})
    assert result['error']['code'] == 'invalid_input'


@pytest.mark.parametrize('name', ['add_parts', 'update_parts', 'add_stock_batch'])
@pytest.mark.parametrize('batch', [[], [{}] * 101, [{'bogus': 'x'}]])
async def test_batch_shape_bounds(registry, name, batch):
    assert (await registry.execute(name, {'items': batch}))['error']['code'] == 'invalid_input'


async def test_add_and_dedupe_completes_ten_values_with_one_tool_call(registry):
    transport = ScriptedTransport([
        ModelTurn(tool_calls=(ToolCall('add_parts', {'items': items()}, 'add'),)),
        ModelTurn('Added all ten values, 20 each.'),
    ])
    repository = registry.service.repository
    runtime = OpenAIResponsesRuntime(transport, registry=registry, store=repository.conversations,
                                     approvals=ApprovalEngine(repository))
    result = await runtime.run('bulk', 'Add and dedupe ten resistor values, 20 each, 0603 1%.')
    assert result.status == 'completed'
    assert len(transport.requests) == 2
    assert len(registry.service.list()) == 10
    assert [event.data['name'] for event in result.events if event.kind == 'tool_call'] == ['add_parts']


async def test_check_inventory_reads_stock_once(registry):
    await registry.execute('add_parts', {'items': items()})
    with patch.object(registry.service.repository.inventory, 'search', wraps=registry.service.repository.inventory.search) as read:
        result = await registry.execute('check_inventory', {'items': [
            {'filters': {'part_category': 'resistor', 'value': value, 'package': '0603'}, 'quantity': 20}
            for value in VALUES]})
    assert result['ok'] and read.call_count == 1
    assert [row['status'] for row in result['result']['items']] == ['sufficient'] * 10


async def test_bulk_update_runtime_previews_all_values_and_uses_one_approval(registry):
    await registry.execute('add_parts', {'items': items()})
    args = {'items': [{'part_id': 1, 'fields': {'quantity': 21}}, {'part_id': 2, 'fields': {'quantity': 22}}]}
    transport = ScriptedTransport([
        ModelTurn(tool_calls=(ToolCall('update_parts', args, 'edit'),)), ModelTurn('Updated both quantities.')])
    repository = registry.service.repository
    runtime = OpenAIResponsesRuntime(transport, registry=registry, store=repository.conversations,
                                     approvals=ApprovalEngine(repository))
    pending = await runtime.run('edit', 'Set resistor quantities to 21 and 22.')
    approvals = [event for event in pending.events if event.kind == 'approval_request']
    assert len(approvals) == 1
    assert '#1: quantity=21' in approvals[0].data['effect']
    assert '#2: quantity=22' in approvals[0].data['effect']
    result = await runtime.run('edit', '', approval_response=ApprovalResponse(approvals[0].data['request_id'], True))
    assert result.status == 'completed'
    assert [registry.service.get(GetPartRequest(identifier)).quantity for identifier in (1, 2)] == [21, 22]


async def test_boolean_search_keeps_suffix_package_stock_and_pending_fields_exact(registry):
    args = {'items': [
        {'part_category': 'bjt', 'profile': 'discrete_ic', 'quantity': 0, 'part_number': 'PBSS5350T,215', 'package': 'SOT23'},
        {'part_category': 'bjt', 'profile': 'discrete_ic', 'quantity': 3, 'part_number': 'PBSS5350T,115', 'package': 'SOT23'},
        {'part_category': 'operational amplifier', 'profile': 'discrete_ic', 'quantity': 4, 'part_number': 'NE5532P', 'package': 'DIP8'},
    ]}
    assert (await registry.execute('add_parts', args))['ok']
    registry.service.repository.inventory.save_pending_review(3, {'part_number': 'PBSS5350T,215'}, [])
    expression = {'any': [{'filters': {'part_number': 'PBSS5350T,215'}},
                          {'all': [{'filters': {'part_category': 'opamp'}}, {'filters': {'package': 'DIP8'}}]}]}
    result = await registry.execute('search_parts', {'expression': expression, 'minimum_quantity': 1})
    assert [row['id'] for row in result['result']['parts']] == [3]
    result = await registry.execute('search_parts', {'expression': {'filters': {'part_number': 'PBSS5350T,215'}}})
    assert [row['id'] for row in result['result']['parts']] == [1]
    assert registry.service.list_pending_reviews()[3]['fields']['part_number']['value'] == 'PBSS5350T,215'
