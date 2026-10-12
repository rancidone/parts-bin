import copy
import json

import httpx
import pytest

from agent_runtime import ImageInput, OpenAIResponsesTransport
from agent_runtime.budgets import (
    MAX_HISTORY_BYTES, MAX_INPUT_BYTES, MAX_OUTPUT_TOKENS,
    MAX_TOOL_RESULT_BYTES, MAX_REASONING_STATE_BYTES, ModelBudgetError, bounded_history, size,
)
from agent_runtime.runtime import ModelRequest


def test_history_keeps_recent_complete_turns_with_multibyte_budget():
    history = tuple(item for i in range(20) for item in (
        {'role': 'user', 'text': f'{i}: ' + '🪿' * 300},
        {'role': 'assistant', 'text': f'reply {i}: ' + '🪿' * 300},
    ))
    kept = bounded_history(history)
    assert size(kept) <= MAX_HISTORY_BYTES
    assert kept[0]['role'] == 'developer'
    assert kept[1]['role'] == 'user'
    assert kept[-2:] == history[-2:]
    assert kept[1:] == history[-len(kept[1:]):]
    assert bounded_history(kept) == kept
    assert len(history) == 40


def test_oversized_latest_turn_does_not_resurrect_older_context():
    history = ({'role': 'user', 'text': 'old'}, {'role': 'assistant', 'text': 'old reply'},
               {'role': 'user', 'text': 'x' * MAX_HISTORY_BYTES},
               {'role': 'assistant', 'text': 'new reply'})
    assert [item['role'] for item in bounded_history(history)] == ['developer']


async def test_large_result_is_explicitly_omitted_without_modifying_saved_outcome():
    captured = []
    result = {'ok': True, 'result': {'parts': [{'description': 'x' * MAX_TOOL_RESULT_BYTES}]}}
    original = copy.deepcopy(result)
    exchange = {'type': 'tool_result', 'call_id': 'a', 'name': 'search_parts', 'arguments': {}, 'result': result}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            captured.append(json.loads(request.content)) or httpx.Response(200, json={'output': []}))) as client:
        await OpenAIResponsesTransport(api_key='fake', model='test', client=client).complete(
            ModelRequest('system', 'find', None, (), (exchange,)))
    output = json.loads(captured[0]['input'][-1]['output'])
    assert output['ok'] is True and output['result_omitted'] is True
    assert 'repeat mutations' in output['message']
    assert result == original
    assert captured[0]['max_output_tokens'] == MAX_OUTPUT_TOKENS


async def test_input_budget_blocks_network_without_dropping_function_pairs():
    captured = []
    exchanges = tuple({'type': 'tool_result', 'call_id': str(i), 'name': 'search_parts',
                      'arguments': {}, 'result': {'ok': True, 'result': 'x' * 10_000}}
                     for i in range(MAX_INPUT_BYTES // 10_000 + 1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            captured.append(request) or httpx.Response(200, json={'output': []}))) as client:
        transport = OpenAIResponsesTransport(api_key='fake', model='test', client=client)
        for user_text, items in [('x' * MAX_INPUT_BYTES, ()), ('find', exchanges)]:
            with pytest.raises(ModelBudgetError, match='input budget'):
                await transport.complete(ModelRequest('system', user_text, None, (), items))
    assert captured == []


async def test_photo_is_not_resent_in_continuation():
    captured = []
    exchange = {'type': 'tool_result', 'call_id': 'a', 'name': 'search_parts',
                'arguments': {}, 'result': {'ok': True}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            captured.append(json.loads(request.content)) or httpx.Response(200, json={'output': []}))) as client:
        await OpenAIResponsesTransport(api_key='fake', model='test', client=client).complete(
            ModelRequest('system', 'identify', ImageInput('image/png', 'AA=='), (), (exchange,)))
    assert captured[0]['input'][0]['content'] == [{'type': 'input_text', 'text': 'identify'}]


async def test_incomplete_response_cannot_execute_partial_mutation():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
            'output': [{'type': 'function_call', 'call_id': 'a', 'name': 'add_stock',
                        'arguments': '{"part_id":1,"quantity":10}'}],
    }))) as client:
        with pytest.raises(ModelBudgetError, match='No partial tool calls'):
            await OpenAIResponsesTransport(api_key='fake', model='test', client=client).complete(
                ModelRequest('system', 'add', None, (), ()))


async def test_opaque_reasoning_has_a_separate_bound_and_is_preserved():
    captured = []
    reasoning = {'type': 'reasoning', 'id': 'rs_1', 'summary': [],
                 'encrypted_content': 'x' * (MAX_INPUT_BYTES + 1)}
    call = {'type': 'function_call', 'call_id': 'a', 'name': 'check_inventory',
            'arguments': '{"items":[{"filters":{"part_number":"NE5532P"}}]}'}
    exchanges = ({'type': 'model_output', 'items': [reasoning, call]},
                 {'type': 'tool_result', 'call_id': 'a', 'name': 'check_inventory',
                  'result': {'ok': True, 'result': {'items': []}}})
    original = copy.deepcopy(exchanges)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            captured.append(json.loads(request.content)) or httpx.Response(200, json={'output': []}))) as client:
        transport = OpenAIResponsesTransport(api_key='fake', model='test', client=client)
        await transport.complete(ModelRequest('system', 'check BOM', None, (), exchanges))
        assert captured[0]['input'][1:3] == [reasoning, call]
        assert exchanges == original
        oversized = {**reasoning, 'encrypted_content': 'x' * (MAX_REASONING_STATE_BYTES + 1)}
        with pytest.raises(ModelBudgetError, match='reasoning budget'):
            await transport.complete(ModelRequest('system', 'check BOM', None, (), (
                {'type': 'model_output', 'items': [oversized, call]}, exchanges[1])))
    assert len(captured) == 1


async def test_whole_bom_fits_budget_with_real_tools_and_preserves_all_outcomes(tmp_path):
    from agent_runtime.runtime import SYSTEM_INSTRUCTIONS
    from db.repository import SQLitePartsBinRepository
    from domain import PartsBinService
    from tools import PartsBinToolRegistry

    registry = PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db')))
    tools = tuple({'type': 'function', 'name': tool['name'], 'description': tool['description'],
                   'parameters': tool['inputSchema'], 'strict': False} for tool in registry.list_tools())
    items = [{'filters': {'part_category': 'capacitor', 'value': f'{i} nF', 'package': '0603'},
              'quantity': 10} for i in range(100)]
    arguments = {'items': items}
    result = await registry.execute('check_inventory', arguments)
    assert result['ok']
    exchanges = [{'type': 'tool_result', 'call_id': 'bom', 'name': 'check_inventory',
                  'arguments': arguments, 'result': result}]
    captured = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            captured.append(json.loads(request.content)) or httpx.Response(200, json={'output': []}))) as client:
        await OpenAIResponsesTransport(api_key='fake', model='test', client=client).complete(
            ModelRequest(SYSTEM_INSTRUCTIONS, json.dumps(items, ensure_ascii=False), None,
                         tools, tuple(exchanges)))
    outputs = [json.loads(item['output']) for item in captured[0]['input']
               if item.get('type') == 'function_call_output']
    assert len(outputs) == 1
    assert all('result_omitted' not in output for output in outputs)
    rows = [row for output in outputs for row in output['result']['items']]
    assert [row['filters'] for row in rows] == [item['filters'] for item in items]


def test_larger_bom_result_budget_remains_bounded_and_other_tools_unchanged():
    from agent_runtime.budgets import MAX_BOM_RESULT_BYTES, model_tool_result
    result = {'ok': True, 'result': {'items': ['x' * (MAX_TOOL_RESULT_BYTES + 1)]}}
    assert model_tool_result(result, tool='check_inventory') == result
    assert model_tool_result(result, tool='search_parts')['result_omitted']
    oversized = {'ok': True, 'result': {'items': ['x' * MAX_BOM_RESULT_BYTES]}}
    omitted = model_tool_result(oversized, tool='check_inventory')
    assert omitted['result_omitted'] and 'smaller list' in omitted['message']
    assert oversized['result']['items']  # Authoritative result is preserved.
