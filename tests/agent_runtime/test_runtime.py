from __future__ import annotations

from collections import deque

import pytest
from db.repository import SQLitePartsBinRepository

from domain import PartsBinService
from tools import PartsBinToolRegistry

from agent_runtime import (ApprovalEngine, ApprovalResponse,
                           ImageInput, ModelTurn, OpenAIResponsesRuntime,
                           RuntimeSelectionError, ToolCall)

from agent_runtime.runtime import ModelRequest
from db.conversations import SQLiteConversationRepository


class ScriptedTransport:
    def __init__(self, turns):
        self.turns = deque(turns)
        self.requests: list[ModelRequest] = []

    async def complete(self, request):
        self.requests.append(request)
        return self.turns.popleft()


def build_runtime(tmp_path, turns, *, limit=8):
    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    registry = PartsBinToolRegistry(PartsBinService(repository))
    store = SQLiteConversationRepository(tmp_path / "conversations.db")
    common = {"registry": registry, "store": store, "approvals": ApprovalEngine(repository), "max_tool_turns": limit}
    transport = ScriptedTransport(turns)
    runtime = OpenAIResponsesRuntime(transport, **common)
    return runtime, transport, store


@pytest.mark.asyncio
async def test_tool_events_and_database_outcome(tmp_path):
    runtime, transport, _store = build_runtime(tmp_path, [
        ModelTurn(tool_calls=(ToolCall("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 2, "value": "10k"}, "a"),)),
        ModelTurn(tool_calls=(ToolCall("search_parts", {"filters": {"value": "10k"}}, "b"),)),
        ModelTurn("Added and found it."),
    ])
    result = await runtime.run("thread", "add a resistor")
    assert result.status == "completed"
    assert [event.kind for event in result.events] == ["user_message", "tool_call", "tool_result", "tool_call", "tool_result", "assistant_text", "completed"]
    assert transport.requests[0].system


@pytest.mark.asyncio
async def test_approval_is_visible_and_must_be_returned_by_same_thread(tmp_path):
    update = ToolCall("update_part", {"part_id": 1, "fields": {"description": "new"}}, "u")
    runtime, _transport, store = build_runtime(tmp_path, [
        ModelTurn(tool_calls=(ToolCall("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"}),)),
        ModelTurn("added"), ModelTurn(tool_calls=(update,)),
        ModelTurn("updated"),
    ])
    await runtime.run("thread", "add")
    pending = await runtime.run("thread", "rename")
    request = next(event for event in pending.events if event.kind == "approval_request")
    resolved = await runtime.run("thread", "yes", approval_response=ApprovalResponse(request.data["request_id"], True))
    assert resolved.status == "completed"
    assert "approval_decision" in [event.kind for event in resolved.events]
    with pytest.raises(RuntimeSelectionError):
        store.create_thread("thread", "local")


@pytest.mark.asyncio
async def test_tool_loop_limit(tmp_path):
    looping, _, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(ToolCall("search_parts", {}),))] * 2, limit=1)
    failed = await looping.run("loop", "search")
    assert failed.status == "failed"
    assert failed.events[-2].data["code"] == "tool_loop_limit"


@pytest.mark.asyncio
async def test_tool_errors_recover_and_images_reach_transport(tmp_path):
    runtime, transport, _ = build_runtime(tmp_path, [
        ModelTurn(tool_calls=(ToolCall("not_a_tool", {}, "bad"),)), ModelTurn("I corrected that."),
    ])
    result = await runtime.run("image", "identify this", image=ImageInput("image/png", "AA=="))
    assert result.status == "completed"
    assert any(event.kind == "tool_result" and event.data["result"]["error"]["code"] == "invalid_input" for event in result.events)
    assert transport.requests[0].image == ImageInput("image/png", "AA==")


@pytest.mark.asyncio
async def test_approved_ic_correction_executes_without_model_repeating_call(tmp_path):
    from domain import GetPartRequest
    runtime, transport, _ = build_runtime(tmp_path, [
        ModelTurn(tool_calls=(ToolCall("add_part", {"part_category": "IC", "profile": "discrete_ic", "quantity": 110, "part_number": "NE5532"}),)),
        ModelTurn("added"),
        ModelTurn(tool_calls=(ToolCall("update_part", {"part_id": 1, "fields": {"quantity": 10, "package": "DIP", "part_category": "operational amplifier", "description": "Dual operational amplifier"}}),)),
        ModelTurn("corrected"),
    ])
    await runtime.run("t", "add")
    pending = await runtime.run("t", "I have 10 not 100 and they are DIP")
    event = next(e for e in pending.events if e.kind == "approval_request")
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 110
    result = await runtime.run("t", "", approval_response=ApprovalResponse(event.data["request_id"], True))
    assert result.status == "completed"
    part = runtime.registry.service.get(GetPartRequest(1))
    assert (part.quantity, part.package, part.part_category) == (10, "DIP", "operational amplifier")
    assert transport.requests[-1].user_text == "I have 10 not 100 and they are DIP"
    assert transport.requests[-1].exchanges[-1]["result"]["ok"]


@pytest.mark.asyncio
async def test_electrical_review_includes_concrete_facts_in_approval_event(tmp_path):
    from domain import AddPartRequest, PartFields
    runtime, _, store = build_runtime(tmp_path, [
        ModelTurn(tool_calls=(ToolCall('apply_specification_review', {'part_id': 1}, 'spec-review'),)),
        ModelTurn('The assertion has been saved as a user assertion.'),
    ])
    service = runtime.registry.service
    part = service.add_part(AddPartRequest(PartFields('resistor', 'passive', 4, value='10k')))
    proposed = {'name': 'tolerance', 'value': '1 %', 'basis': 'maximum', 'conditions': {},
                'evidence': {'kind': 'user_assertion', 'excerpt': 'I said these are 1% resistors.'}}
    service.stage_specifications(part, [proposed])
    pending = await runtime.run('spec-chat', 'Accept the tolerance assertion.')
    event = next(event for event in pending.events if event.kind == 'approval_request')
    assert event.data['specification_review']['facts'] == [proposed]
    result = await runtime.run('spec-chat', '', approval_response=ApprovalResponse(event.data['request_id'], True))
    assert result.status == 'completed'
    assert service.get_specifications(part.id)['facts'] == [proposed]
    assert any(event.kind == 'approval_decision' for event in store.events('spec-chat'))


async def test_history_budget_changes_only_model_context(tmp_path):
    from agent_runtime import ConversationEvent
    from agent_runtime.budgets import MAX_HISTORY_BYTES, size
    runtime, transport, store = build_runtime(tmp_path, [ModelTurn('answer')])
    store.create_thread('long', 'openai')
    for i in range(30):
        store.append(ConversationEvent('user_message', 'long', 'openai', {'text': f'{i}: ' + 'x' * 1000}))
        store.append(ConversationEvent('assistant_text', 'long', 'openai', {'text': f'reply {i}'}))
    before = store.events('long')
    await runtime.run('long', 'latest')
    assert size(transport.requests[0].history) <= MAX_HISTORY_BYTES
    assert transport.requests[0].history[0]['role'] == 'developer'
    assert transport.requests[0].history[-1]['text'] == 'reply 29'
    assert store.events('long')[:len(before)] == before


async def test_budget_failure_finishes_execution_and_preserves_completed_mutation(tmp_path):
    from domain import GetPartRequest
    from agent_runtime.budgets import ModelBudgetError
    runtime, transport, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(ToolCall(
        'add_part', {'part_category': 'resistor', 'profile': 'passive', 'quantity': 2, 'value': '10k'}, 'a'),))])
    original_complete = transport.complete
    async def complete(request):
        if transport.requests:
            raise ModelBudgetError('context_budget_exceeded', 'narrow the request')
        return await original_complete(request)
    transport.complete = complete
    result = await runtime.run('budget', 'add resistor')
    assert result.status == 'failed'
    assert result.events[-2].data['code'] == 'context_budget_exceeded'
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 2
    assert any(event.kind == 'tool_result' and event.data['result']['ok'] for event in store.events('budget'))
    replay = await runtime.run('budget', 'resume', execution_id=result.execution_id)
    assert replay.status == 'failed'
    assert len(runtime.registry.service.list()) == 1


@pytest.mark.asyncio
async def test_pasted_bom_batch_results_reach_chat_without_mutating_stock(tmp_path):
    from domain import AddPartRequest, PartFields
    from agent_runtime.budgets import model_tool_result

    items = [
        {'filters': {'part_number': 'PBSS5350T,215'}, 'quantity': 4},
        {'filters': {'part_number': 'PBSS5350T,115'}, 'quantity': 2},
        {'filters': {'part_number': 'NE5532P'}, 'quantity': 3},
    ]
    runtime, transport, store = build_runtime(tmp_path, [
        ModelTurn(tool_calls=(ToolCall('check_inventory', {'items': items}, 'bom'),)),
        ModelTurn('PBSS5350T,215: 8 available. PBSS5350T,115: missing. NE5532P: 2 available, short 1.'),
    ])
    for number, quantity in [('PBSS5350T,215', 8), ('NE5532P', 2)]:
        runtime.registry.service.add_part(AddPartRequest(PartFields(
            part_category='IC', profile='discrete_ic', part_number=number, quantity=quantity)))
    before = runtime.registry.service.list()
    result = await runtime.run('bom', 'Check this BOM:\nMPN\tQty\nPBSS5350T,215\t4\nPBSS5350T,115\t2\nNE5532P\t3')
    assert result.status == 'completed'
    exchange = transport.requests[-1].exchanges[-1]['result']
    assert [item['status'] for item in exchange['result']['items']] == ['sufficient', 'missing', 'insufficient']
    assert not model_tool_result(exchange).get('result_omitted')
    assert not any(event.kind == 'approval_request' for event in store.events('bom'))
    assert runtime.registry.service.list() == before
