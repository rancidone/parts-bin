from __future__ import annotations
from db.repository import SQLitePartsBinRepository

from agent_runtime import AgentGateway, AgentTelemetry, ApprovalEngine, ModelTurn, OpenAIResponsesRuntime, ToolCall
from db.conversations import SQLiteConversationRepository
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


async def test_agent_telemetry_redacts_private_content(tmp_path):
    captured = CapturedTelemetry()
    telemetry = AgentTelemetry(captured.emit)
    store = SQLiteConversationRepository(tmp_path / "events.db")
    registry = PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db")))
    runtime = OpenAIResponsesRuntime(Turns([
        ModelTurn(tool_calls=(ToolCall("search_parts", {"filters": {"part_number": "private-part"}}),)), ModelTurn("private answer"),
    ]), registry=registry, store=store, approvals=ApprovalEngine(registry.service.repository), telemetry=telemetry)
    gateway = AgentGateway(store, lambda: runtime, telemetry=telemetry)
    thread = gateway.create_thread()
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
    store = SQLiteConversationRepository(tmp_path / "events.db")
    registry = PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db")))
    runtime = OpenAIResponsesRuntime(Turns([ModelTurn(tool_calls=(ToolCall("unknown", {"credential": "never"}),)), ModelTurn("done")]),
                                    registry=registry, store=store, approvals=ApprovalEngine(registry.service.repository), telemetry=telemetry)
    await runtime.run("one", "secret")
    looping = OpenAIResponsesRuntime(Turns([ModelTurn(tool_calls=(ToolCall("search_parts", {}),))]), registry=registry,
                                     store=store, approvals=ApprovalEngine(registry.service.repository), telemetry=telemetry, max_tool_turns=1)
    await looping.run("two", "secret")
    emitted = {event for event, _ in captured.items}
    assert "agent_tool_error" in emitted and "agent_loop_limit" in emitted


async def test_budget_failures_emit_exact_code_once_and_preserve_saved_failure(tmp_path):
    from agent_runtime.budgets import ModelBudgetError
    from .test_runtime import build_runtime

    for code in ('context_budget_exceeded', 'model_response_incomplete'):
        captured = CapturedTelemetry()
        (tmp_path / code).mkdir()
        runtime, _, store = build_runtime(tmp_path / code, [])
        runtime.telemetry = AgentTelemetry(captured.emit)

        class BudgetTransport:
            async def complete(self, request):
                raise ModelBudgetError(code, 'private error detail')

        runtime.transport = BudgetTransport()
        result = await runtime.run('private-thread', 'private prompt')
        assert result.status == 'failed'
        assert next(event for event in store.events('private-thread') if event.kind == 'error').data['code'] == code
        failures = [fields for event, fields in captured.items if event == 'agent_runtime_error']
        assert len(failures) == 1 and failures[0]['error_code'] == code
        assert captured.items[-1][1]['status'] == 'failed'
        assert 'private' not in repr(captured.items)


async def test_cancelled_model_call_releases_lease_and_reports_interruption(tmp_path):
    import asyncio
    from .test_runtime import build_runtime

    runtime, _, store = build_runtime(tmp_path, [])
    captured = CapturedTelemetry()
    runtime.telemetry = AgentTelemetry(captured.emit)
    entered = asyncio.Event()

    class PausedTransport:
        async def complete(self, request):
            entered.set()
            await asyncio.Event().wait()

    runtime.transport = PausedTransport()
    task = asyncio.create_task(runtime.run('thread', 'hello'))
    await entered.wait()
    execution = store.events('thread')[0].data['execution_id']
    task.cancel()
    import pytest
    with pytest.raises(asyncio.CancelledError):
        await task
    record = runtime.registry.service.repository.executions.claim('thread', execution, 'new-worker')
    assert record.status == 'ready'
    runtime.registry.service.repository.executions.release(execution, 'new-worker')
    assert captured.items[-2][1]['error_code'] == 'execution_interrupted'
    assert captured.items[-1][1]['status'] == 'interrupted'
