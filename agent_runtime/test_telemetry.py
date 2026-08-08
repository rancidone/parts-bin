from __future__ import annotations

from agent_runtime import AgentGateway, AgentTelemetry, ApprovalEngine, ConversationStore, ModelTurn, OpenAIResponsesRuntime, ToolCall
from domain import PartsBinService
from tools import PartsBinToolRegistry


class CapturedTelemetry:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []

    def emit(self, event: str, **fields) -> None:
        self.items.append((event, fields))


class Turns:
    def __init__(self, turns):
        self.turns = iter(turns)

    async def complete(self, _request):
        return next(self.turns)


async def test_agent_telemetry_is_cross_runtime_safe_and_complete(tmp_path):
    captured = CapturedTelemetry()
    telemetry = AgentTelemetry(captured.emit)
    store = ConversationStore(tmp_path / "events.db")
    registry = PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db"))
    runtime = OpenAIResponsesRuntime(Turns([
        ModelTurn(tool_calls=(ToolCall("search_parts", {"filters": {"part_number": "private-part"}}),)), ModelTurn("private answer"),
    ]), registry=registry, store=store, approvals=ApprovalEngine(), telemetry=telemetry)
    gateway = AgentGateway(store, lambda _: runtime, telemetry=telemetry)
    thread = gateway.create_thread("openai")
    await gateway.submit(thread, "private prompt")
    encoded = repr(captured.items)
    assert "private-part" not in encoded and "private prompt" not in encoded and "private answer" not in encoded
    events = [event for event, _ in captured.items]
    assert events == ["agent_runtime_selected", "agent_tool_started", "agent_tool_finished", "agent_turn_finished"]
    tool = captured.items[1][1]
    assert tool["argument_keys"] == ["filters"]
    assert "arguments" not in tool


async def test_telemetry_records_tool_error_approval_loop_and_runtime_failure(tmp_path):
    captured = CapturedTelemetry()
    telemetry = AgentTelemetry(captured.emit)
    store = ConversationStore(tmp_path / "events.db")
    registry = PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db"))
    runtime = OpenAIResponsesRuntime(Turns([ModelTurn(tool_calls=(ToolCall("unknown", {"credential": "never"}),)), ModelTurn("done")]),
                                    registry=registry, store=store, approvals=ApprovalEngine(), telemetry=telemetry)
    await runtime.run("one", "secret")
    looping = OpenAIResponsesRuntime(Turns([ModelTurn(tool_calls=(ToolCall("search_parts", {}),))]), registry=registry,
                                     store=store, approvals=ApprovalEngine(), telemetry=telemetry, max_tool_turns=1)
    await looping.run("two", "secret")
    emitted = {event for event, _ in captured.items}
    assert "agent_tool_error" in emitted and "agent_loop_limit" in emitted
