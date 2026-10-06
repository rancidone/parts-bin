from __future__ import annotations

from collections import deque

import pytest

from domain import PartsBinService
from tools import PartsBinToolRegistry
from tools.mcp_server import MCPServer

from agent_runtime import (ApprovalEngine, ApprovalResponse, CodexAppServerRuntime,
                           ConversationStore, LocalOpenAICompatibleRuntime,
                           ImageInput, ModelTurn, OpenAIResponsesRuntime, PartsBinMCPClient,
                           RuntimeSelectionError, ToolCall)
from agent_runtime.runtime import ModelRequest


class ScriptedTransport:
    def __init__(self, turns):
        self.turns = deque(turns)
        self.requests: list[ModelRequest] = []

    async def complete(self, request):
        self.requests.append(request)
        return self.turns.popleft()


def build_runtime(kind, tmp_path, turns, *, native=True, limit=8):
    registry = PartsBinToolRegistry(PartsBinService(tmp_path / f"{kind}.db"))
    store = ConversationStore(tmp_path / "conversations.db")
    common = {"registry": registry, "store": store, "approvals": ApprovalEngine(), "max_tool_turns": limit}
    transport = ScriptedTransport(turns)
    if kind == "openai":
        runtime = OpenAIResponsesRuntime(transport, **common)
    elif kind == "local":
        runtime = LocalOpenAICompatibleRuntime(transport, supports_native_tools=native, **common)
    else:
        runtime = CodexAppServerRuntime(transport, mcp_client=PartsBinMCPClient(MCPServer(registry)), **common)
    return runtime, transport, store


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codex", "openai", "local"])
async def test_runtimes_share_tool_events_and_database_outcome(tmp_path, kind):
    runtime, transport, _store = build_runtime(kind, tmp_path, [
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
    runtime, _transport, store = build_runtime("openai", tmp_path, [
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
async def test_local_json_envelope_and_loop_limit(tmp_path):
    runtime, transport, _ = build_runtime("local", tmp_path, [
        ModelTurn('{"type":"parts_bin_tool_call","name":"search_parts","arguments":{}}'),
        ModelTurn("done"),
    ], native=False)
    assert (await runtime.run("json", "what do I have")).status == "completed"
    assert transport.requests[0].json_tool_envelope is True

    looping, _, _ = build_runtime("openai", tmp_path, [ModelTurn(tool_calls=(ToolCall("search_parts", {}),))] * 2, limit=1)
    failed = await looping.run("loop", "search")
    assert failed.status == "failed"
    assert failed.events[-2].data["code"] == "tool_loop_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codex", "openai", "local"])
async def test_tool_errors_recover_and_images_reach_each_runtime(tmp_path, kind):
    runtime, transport, _ = build_runtime(kind, tmp_path, [
        ModelTurn(tool_calls=(ToolCall("not_a_tool", {}, "bad"),)), ModelTurn("I corrected that."),
    ])
    result = await runtime.run("image", "identify this", image=ImageInput("image/png", "AA=="))
    assert result.status == "completed"
    assert any(event.kind == "tool_result" and event.data["result"]["error"]["code"] == "invalid_input" for event in result.events)
    assert transport.requests[0].image == ImageInput("image/png", "AA==")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codex", "openai", "local"])
async def test_approved_ic_correction_executes_without_model_repeating_call(tmp_path, kind):
    from domain import GetPartRequest
    runtime, transport, _ = build_runtime(kind, tmp_path, [
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
    assert transport.requests[-1].exchanges[0]["result"]["ok"]
