"""Bounded worker invocations across independent runtime instances."""

import json
from unittest.mock import AsyncMock

import pytest

from agent_runtime import ApprovalResponse, ImageInput, ModelTurn, ToolCall
from domain import GetPartRequest
from domain.repositories import ExecutionBusy
from .test_runtime import build_runtime


async def add_stock_fixture(runtime):
    await runtime.registry.execute("add_part", {
        "part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"})


async def test_each_model_and_tool_stage_can_run_in_a_fresh_worker(tmp_path):
    reasoning = ({"type": "reasoning", "id": "reason", "summary": []},)
    first, transport, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("add_stock", {"part_id": 1, "quantity": 2}, "one"),
        ToolCall("add_stock", {"part_id": 1, "quantity": 3}, "two")), response_output=reasoning)])
    await add_stock_fixture(first)
    result = await first.advance("thread", "add five")
    execution_id = result.execution_id
    assert result.status == "ready"
    assert len(transport.requests) == 1
    assert first.registry.service.get(GetPartRequest(1)).quantity == 1
    assert not any(e.kind == "completed" for e in store.events("thread"))

    for expected_quantity in (3, 6):
        worker, unused, _ = build_runtime(tmp_path, [])
        result = await worker.advance("thread", execution_id=execution_id)
        assert result.status == "ready"
        assert not unused.requests
        assert worker.registry.service.get(GetPartRequest(1)).quantity == expected_quantity

    final, transport, _ = build_runtime(tmp_path, [ModelTurn("Added five")])
    result = await final.advance("thread", execution_id=execution_id)
    assert result.status == "completed"
    request = transport.requests[0]
    assert request.user_text == "add five"
    assert request.exchanges[0]["items"] == list(reasoning)
    assert [e["call_id"] for e in request.exchanges if e["type"] == "tool_result"] == ["one", "two"]
    replay, unused, _ = build_runtime(tmp_path, [])
    assert await replay.advance("thread", execution_id=execution_id) == result
    assert not unused.requests
    assert len([e for e in store.events("thread") if e.kind == "user_message"]) == 1
    assert len([e for e in store.events("thread") if e.kind == "tool_result"]) == 2


@pytest.mark.parametrize("approved", [True, False])
async def test_approval_wait_and_duplicate_decision_across_workers(tmp_path, approved):
    runtime, _, store = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("update_part", {"part_id": 1, "fields": {"description": "new"}}, "edit"),))])
    await add_stock_fixture(runtime)
    result = await runtime.advance("thread", "rename")
    execution_id = result.execution_id
    waiting, unused, _ = build_runtime(tmp_path, [])
    result = await waiting.advance("thread", execution_id=execution_id)
    assert result.status == "awaiting_approval"
    request_id = next(e.data["request_id"] for e in result.events if e.kind == "approval_request")
    assert (await waiting.advance("thread", execution_id=execution_id)).status == "awaiting_approval"
    assert not unused.requests
    decision = ApprovalResponse(request_id, approved)
    worker, _, _ = build_runtime(tmp_path, [])
    result = await worker.advance("thread", approval_response=decision)
    assert result.status == "ready"
    assert worker.registry.service.get(GetPartRequest(1)).description == ("new" if approved else None)
    final, transport, _ = build_runtime(tmp_path, [ModelTurn("Done")])
    result = await final.advance("thread", approval_response=decision)
    assert result.status == "completed"
    assert len(transport.requests) == 1
    replay, unused, _ = build_runtime(tmp_path, [])
    assert await replay.advance("thread", approval_response=decision) == result
    assert not unused.requests
    assert len([e for e in store.events("thread") if e.kind == "approval_decision"]) == 1
    with pytest.raises(ValueError, match="already been recorded"):
        await replay.advance("thread", approval_response=ApprovalResponse(request_id, not approved))


async def test_photo_is_only_needed_for_first_model_stage(tmp_path):
    runtime, transport, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("list_categories", {}, "categories"),))])
    image = ImageInput("image/png", "PRIVATE_IMAGE")
    result = await runtime.advance("thread", "identify", image=image)
    assert transport.requests[0].image == image
    execution_id = result.execution_id
    repository = runtime.registry.service.repository
    record = repository.executions.claim("thread", execution_id, "inspect")
    assert "PRIVATE_IMAGE" not in json.dumps(record.context)
    repository.executions.release(execution_id, "inspect")
    worker, unused, _ = build_runtime(tmp_path, [])
    assert (await worker.advance("thread", execution_id=execution_id)).status == "ready"
    assert not unused.requests
    final, transport, _ = build_runtime(tmp_path, [ModelTurn("Identified")])
    assert (await final.advance("thread", execution_id=execution_id)).status == "completed"
    assert transport.requests[0].image is None


async def test_model_round_limit_applies_across_invocations(tmp_path):
    runtime, _, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("list_categories", {}, "categories"),))], limit=1)
    result = await runtime.advance("thread", "categories")
    worker, _, _ = build_runtime(tmp_path, [], limit=1)
    assert (await worker.advance("thread", execution_id=result.execution_id)).status == "ready"
    result = await worker.advance("thread", execution_id=result.execution_id)
    assert result.status == "failed"
    assert result.events[-2].data["code"] == "tool_loop_limit"


async def test_crash_after_commit_before_checkpoint_does_not_repeat_mutation(tmp_path, monkeypatch):
    runtime, _, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("add_stock", {"part_id": 1, "quantity": 2}, "stock"),))])
    await add_stock_fixture(runtime)
    result = await runtime.advance("thread", "add two")
    original = runtime.registry.execute

    async def commit_then_crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("worker lost")

    monkeypatch.setattr(runtime.registry, "execute", commit_then_crash)
    with pytest.raises(RuntimeError, match="worker lost"):
        await runtime.advance("thread", execution_id=result.execution_id)
    replacement, unused, _ = build_runtime(tmp_path, [])
    assert (await replacement.advance("thread", execution_id=result.execution_id)).status == "ready"
    assert replacement.registry.service.get(GetPartRequest(1)).quantity == 3
    assert not unused.requests


async def test_step_respects_live_execution_lease(tmp_path):
    runtime, _, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("list_categories", {}, "categories"),))])
    result = await runtime.advance("thread", "categories")
    repository = runtime.registry.service.repository
    repository.executions.claim("thread", result.execution_id, "other")
    worker, unused, _ = build_runtime(tmp_path, [])
    with pytest.raises(ExecutionBusy):
        await worker.advance("thread", execution_id=result.execution_id)
    assert not unused.requests
    repository.executions.release(result.execution_id, "other")


async def test_interrupted_retrieval_is_not_repeated_by_next_worker(tmp_path, monkeypatch):
    import asyncio

    runtime, _, _ = build_runtime(tmp_path, [ModelTurn(tool_calls=(
        ToolCall("lookup_part_specs", {"part_id": 1}, "lookup"),))])
    await add_stock_fixture(runtime)
    result = await runtime.advance("thread", "fetch specs")
    monkeypatch.setattr(runtime.registry.service, "fetch_and_stage_specs",
                        AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await runtime.advance("thread", execution_id=result.execution_id)
    replacement, _, _ = build_runtime(tmp_path, [])
    lookup = AsyncMock()
    monkeypatch.setattr(replacement.registry.service, "fetch_and_stage_specs", lookup)
    result = await replacement.advance("thread", execution_id=result.execution_id)
    assert result.status == "ready"
    lookup.assert_not_awaited()
    tool = next(e for e in result.events if e.kind == "tool_result")
    assert tool.data["result"]["error"]["code"] == "retrieval_interrupted"
