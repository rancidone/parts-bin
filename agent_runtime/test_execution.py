"""Restart and acknowledgement failure windows for inventory agent executions."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent_runtime import ApprovalResponse, ImageInput, ModelTurn, ToolCall
from domain import GetPartRequest
from domain.repositories import ExecutionUnavailable
from tools import ToolExecutionContext
from .test_runtime import build_runtime


def saved_execution(store, thread="thread"):
    return store.events(thread)[0].data["execution_id"]


async def test_model_output_checkpoint_precedes_tools_and_survives_restart(tmp_path):
    reasoning = ({"type": "reasoning", "id": "reason", "summary": []},
                 {"type": "function_call", "call_id": "stock", "name": "add_stock",
                  "arguments": '{"part_id":1,"quantity":3}'})
    runtime, transport, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("add_stock", {"part_id": 1, "quantity": 3}, "stock"),), response_output=reasoning)])
    await runtime.registry.execute("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"})

    def crash(event):
        if event.kind == "tool_call":
            raise RuntimeError("worker stopped before tool")

    with pytest.raises(RuntimeError):
        await runtime.run("thread", "three more", on_event=crash)
    restarted, resumed_transport, _ = build_runtime(tmp_path, [ModelTurn("Stock added")])
    result = await restarted.run("thread", "", execution_id=saved_execution(store))
    assert result.status == "completed"
    assert len(transport.requests) == len(resumed_transport.requests) == 1
    assert resumed_transport.requests[0].exchanges[0] == {"type": "model_output", "items": list(reasoning)}
    assert restarted.registry.service.get(GetPartRequest(1)).quantity == 4


async def test_stock_commit_before_checkpoint_and_event_is_applied_once(tmp_path, monkeypatch):
    runtime, _, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("add_stock", {"part_id": 1, "quantity": 3}, "stock"),))])
    await runtime.registry.execute("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"})
    original = runtime.registry.execute

    async def commit_then_stop(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("stopped after commit")

    monkeypatch.setattr(runtime.registry, "execute", commit_then_stop)
    with pytest.raises(RuntimeError):
        await runtime.run("thread", "three more")
    restarted, _, _ = build_runtime(tmp_path, [ModelTurn("Done")])
    result = await restarted.run("thread", "", execution_id=saved_execution(store))
    assert result.status == "completed"
    assert restarted.registry.service.get(GetPartRequest(1)).quantity == 4
    results = [e for e in store.events("thread") if e.kind == "tool_result"]
    assert len(results) == 1
    replay = await restarted.run("thread", "", execution_id=result.execution_id)
    assert replay.events == result.events


async def test_approval_preserves_reasoning_and_remaining_calls(tmp_path):
    reasoning = ({"type": "reasoning", "id": "r", "summary": []},)
    runtime, _, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("update_part", {"part_id": 1, "fields": {"description": "new"}}, "update"),
        ToolCall("add_stock", {"part_id": 1, "quantity": 2}, "stock")), response_output=reasoning)])
    await runtime.registry.execute("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"})
    pending = await runtime.run("thread", "rename and add two")
    request = next(e.data["request_id"] for e in pending.events if e.kind == "approval_request")
    restarted, transport, _ = build_runtime(tmp_path, [ModelTurn("Updated")])
    result = await restarted.run("thread", "", approval_response=ApprovalResponse(request, True))
    assert result.status == "completed"
    assert transport.requests[0].user_text == "rename and add two"
    exchanges = transport.requests[0].exchanges
    assert exchanges[0]["items"] == list(reasoning)
    assert [e["call_id"] for e in exchanges if e["type"] == "tool_result"] == ["update", "stock"]
    part = restarted.registry.service.get(GetPartRequest(1))
    assert (part.description, part.quantity) == ("new", 3)
    with pytest.raises(ValueError, match="already been recorded"):
        await restarted.run("thread", "", approval_response=ApprovalResponse(request, False))


async def test_declined_approval_returns_output_without_mutation(tmp_path):
    runtime, transport, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("delete_part", {"part_id": 1}, "delete"),)), ModelTurn("Kept it")])
    await runtime.registry.execute("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"})
    pending = await runtime.run("thread", "delete")
    request = next(e.data["request_id"] for e in pending.events if e.kind == "approval_request")
    await runtime.run("thread", "", approval_response=ApprovalResponse(request, False))
    assert len(runtime.registry.service.list()) == 1
    assert transport.requests[-1].exchanges[-1]["result"]["error"]["code"] == "approval_denied"


async def test_interrupted_add_does_not_repeat_paid_enrichment(tmp_path, monkeypatch):
    runtime, _, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(ToolCall("add_part", {
        "part_category": "IC", "profile": "discrete_ic", "quantity": 2, "part_number": "NE5532"}, "add"),))])
    lookup = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(runtime.registry.service, "fetch_and_stage_specs", lookup)
    with pytest.raises(asyncio.CancelledError):
        await runtime.run("thread", "two NE5532")
    restarted, _, _ = build_runtime(tmp_path, [ModelTurn("Added; lookup interrupted")])
    next_lookup = AsyncMock()
    monkeypatch.setattr(restarted.registry.service, "fetch_and_stage_specs", next_lookup)
    result = await restarted.run("thread", "", execution_id=saved_execution(store))
    assert lookup.await_count == 1
    next_lookup.assert_not_awaited()
    assert len(restarted.registry.service.list()) == 1
    tool = next(e for e in result.events if e.kind == "tool_result")
    assert tool.data["result"]["result"]["enrichment"]["status"] == "interrupted"


async def test_interrupted_explicit_lookup_is_not_automatically_repeated(tmp_path, monkeypatch):
    runtime, _, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("lookup_part_specs", {"part_id": 1}, "lookup"),))])
    lookup = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(runtime.registry.service, "fetch_and_stage_specs", lookup)
    with pytest.raises(asyncio.CancelledError):
        await runtime.run("thread", "look up specifications")
    restarted, _, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("lookup_part_specs", {"part_id": 1}, "automatic_retry"),)), ModelTurn("Lookup interrupted")])
    replacement = AsyncMock()
    monkeypatch.setattr(restarted.registry.service, "fetch_and_stage_specs", replacement)
    result = await restarted.run("thread", "", execution_id=saved_execution(store))
    replacement.assert_not_awaited()
    tool = next(e for e in result.events if e.kind == "tool_result")
    assert tool.data["result"]["error"]["code"] == "retrieval_interrupted"


async def test_operation_ledger_failure_rolls_back_stock(tmp_path, monkeypatch):
    from db.execution import SQLiteOperationRepository

    runtime, _, _ = build_runtime(tmp_path, [])
    await runtime.registry.execute("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"})

    def fail(_repository, _operation):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(SQLiteOperationRepository, "insert", fail)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await runtime.registry.execute("add_stock", {"part_id": 1, "quantity": 2},
                                       context=ToolExecutionContext(operation_id="stock"))
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 1
    assert runtime.registry.service.repository.operations.get("stock") is None


async def test_expired_worker_is_fenced_and_replacement_can_claim(tmp_path, monkeypatch):
    from db import execution

    runtime, _, _ = build_runtime(tmp_path, [])
    repo = runtime.registry.service.repository
    now = execution.time.time()
    monkeypatch.setattr(execution.time, "time", lambda: now)
    repo.executions.claim("thread", "execution", "old", {})
    monkeypatch.setattr(execution.time, "time", lambda: now + execution.LEASE_SECONDS + 1)
    with pytest.raises(ExecutionUnavailable):
        repo.executions.save("execution", "old", {}, "ready")
    assert repo.executions.claim("thread", "execution", "new").execution_id == "execution"
    with pytest.raises(ExecutionUnavailable):
        repo.executions.assert_owned("execution", "old")
    repo.executions.assert_owned("execution", "new")


async def test_image_never_enters_checkpoint_and_restart_requires_resubmission(tmp_path):
    runtime, transport, store = build_runtime(tmp_path, [])
    transport.complete = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await runtime.run("thread", "identify", image=ImageInput("image/png", "SECRET_IMAGE_BYTES"))
    execution_id = saved_execution(store)
    repo = runtime.registry.service.repository
    record = repo.executions.claim("thread", execution_id, "inspector")
    assert "SECRET_IMAGE_BYTES" not in json.dumps(record.context)
    repo.executions.release(execution_id, "inspector")
    restarted, replacement, _ = build_runtime(tmp_path, [])
    result = await restarted.run("thread", "", execution_id=execution_id)
    assert result.status == "failed"
    assert result.events[-2].data["code"] == "image_resubmission_required"
    assert not replacement.requests


async def test_execution_leases_thread_identity_and_distinct_operations(tmp_path):
    runtime, _, _ = build_runtime(tmp_path, [])
    repo = runtime.registry.service.repository
    args = {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"}
    part = await runtime.registry.execute("add_part", args)
    context = ToolExecutionContext(operation_id="one")
    await runtime.registry.execute("add_stock", {"part_id": part["result"]["id"], "quantity": 2}, context=context)
    await runtime.registry.execute("add_stock", {"part_id": 1, "quantity": 2}, context=context)
    await runtime.registry.execute("add_stock", {"part_id": 1, "quantity": 2}, context=ToolExecutionContext(operation_id="two"))
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 5
    result = await runtime.registry.execute("add_stock", {"part_id": 1, "quantity": 3}, context=context)
    assert result["error"]["code"] == "conflict"
    repo.executions.claim("thread", "execution", "owner", {})
    with pytest.raises(ExecutionUnavailable, match="already running"):
        repo.executions.claim("thread", "execution", "other")
    with pytest.raises(ExecutionUnavailable, match="Unknown"):
        repo.executions.claim("wrong-thread", "execution", "other")
    with pytest.raises(ExecutionUnavailable, match="unfinished"):
        repo.executions.claim("thread", "new", "other", {})
    stale = await runtime.registry.execute("add_stock", {"part_id": 1, "quantity": 2},
        context=ToolExecutionContext(operation_id="stale", execution_id="execution", worker_id="other"))
    assert not stale["ok"]
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 5
