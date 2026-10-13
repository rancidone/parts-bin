"""Submit, exit, and collect Responses from independent worker instances."""

import asyncio
import json
from collections import deque

import httpx
import pytest

from agent_runtime import ApprovalEngine, ApprovalResponse, ImageInput, OpenAIResponsesRuntime, OpenAIResponsesTransport
from application import create_services
from tests.storage import TestConversations
from tests.storage import TestRepository
from domain import GetPartRequest, PartsBinService
from domain.repositories import ExecutionBusy, ExecutionUnavailable
from tools import PartsBinToolRegistry


class Provider:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []
        self.posts = 0
        self.error = None

    def handle(self, request):
        self.requests.append(request)
        if request.method == "POST":
            self.posts += 1
            if self.error:
                raise self.error
            return httpx.Response(200, json={"id": "resp_" + str(self.posts), "status": "queued"})
        if self.error:
            raise self.error
        payload = self.responses.popleft()
        if isinstance(payload, int):
            return httpx.Response(payload)
        return httpx.Response(200, json={"id": request.url.path.split("/")[-1], **payload})


def worker_at(tmp_path, provider):
    repository = TestRepository(tmp_path / "inventory.db")
    store = TestConversations(tmp_path / "events.db")
    transport = OpenAIResponsesTransport(api_key="test-only", model="test-model",
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)))
    runtime = OpenAIResponsesRuntime(transport, registry=PartsBinToolRegistry(PartsBinService(repository)),
                                    store=store, approvals=ApprovalEngine(repository))
    return runtime, transport, store


def stock_call(quantity=2):
    return {"type": "function_call", "name": "add_stock", "call_id": "stock",
            "arguments": json.dumps({"part_id": 1, "quantity": quantity})}


async def test_prepare_submit_wait_collect_mutate_and_finish_in_fresh_workers(tmp_path):
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
    provider = Provider([
        {"status": "in_progress", "output": [stock_call(100)]},
        {"status": "completed", "output": [reasoning, stock_call()]},
        {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Added two"}]}]},
    ])
    runtime, transport, store = worker_at(tmp_path, provider)
    await runtime.registry.execute("add_part", {"part_category": "resistor", "profile": "passive",
        "quantity": 1, "value": "10k"})
    image = ImageInput("image/png", "PRIVATE_IMAGE")
    assert runtime.prepare("thread", "stable-request", "add two", image=image) == "stable-request"
    assert not provider.requests
    assert not store.events("thread")
    # Lost intake acknowledgement: repeat the same identity and payload.
    assert runtime.prepare("thread", "stable-request", "add two", image=image) == "stable-request"
    result = await runtime.advance_background("thread", "stable-request", image=image)
    assert result.status == "waiting_model"
    assert len(provider.requests) == provider.posts == 1
    body = json.loads(provider.requests[0].content)
    assert body["background"] is True and body["store"] is False
    assert body["input"][-1]["content"][-1]["image_url"] == "data:image/png;base64,PRIVATE_IMAGE"
    await transport.close()

    for expected_status, expected_quantity in [
        ("waiting_model", 1), ("ready", 1), ("ready", 3),
        ("waiting_model", 3), ("completed", 3),
    ]:
        runtime, transport, _ = worker_at(tmp_path, provider)
        result = await runtime.advance_background("thread", "stable-request")
        assert result.status == expected_status
        assert runtime.registry.service.get(GetPartRequest(1)).quantity == expected_quantity
        await transport.close()
    assert provider.posts == 2
    continuation = json.loads(next(request.content for request in reversed(provider.requests)
                                   if request.method == "POST"))
    assert continuation["input"][1:3] == [reasoning, stock_call()]
    assert not any(part.get("type") == "input_image" for item in continuation["input"]
                   if isinstance(item.get("content"), list) for part in item["content"])
    record = runtime.registry.service.repository.executions.claim("thread", "stable-request", "inspect")
    assert "PRIVATE_IMAGE" not in json.dumps(record.context)
    assert "opaque" not in repr(store.events("thread"))
    count = len(provider.requests)
    runtime, transport, _ = worker_at(tmp_path, provider)
    assert runtime.prepare("thread", "stable-request", "add two", image=image) == "stable-request"
    assert await runtime.advance_background("thread", "stable-request") == result
    assert len(provider.requests) == count
    assert len([event for event in store.events("thread") if event.kind == "tool_result"]) == 1
    await transport.close()


async def test_duplicate_intake_cannot_change_payload_or_reset_active_progress(tmp_path):
    provider = Provider([])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "hello")
    repository = runtime.registry.service.repository
    original = repository.executions.claim("thread", "request", "worker")
    assert runtime.prepare("thread", "request", "hello") == "request"
    repository.executions.assert_owned("request", "worker")
    with pytest.raises(ExecutionUnavailable, match="different request"):
        runtime.prepare("thread", "request", "different")
    with pytest.raises(ExecutionUnavailable, match="different request"):
        runtime.prepare("other-thread", "request", "hello")
    with pytest.raises(ExecutionBusy):
        await runtime.advance_background("thread", "request")
    repository.executions.release("request", "worker")
    assert original.context["user_text"] == "hello"
    assert not provider.requests
    await transport.close()


@pytest.mark.parametrize("failure", [httpx.ConnectError("lost acknowledgement"), asyncio.CancelledError()])
async def test_uncertain_submission_is_never_resubmitted(tmp_path, failure):
    provider = Provider([])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "hello")
    provider.error = failure
    with pytest.raises(type(failure)):
        await runtime.advance_background("thread", "request")
    await transport.close()
    provider.error = None
    runtime, transport, _ = worker_at(tmp_path, provider)
    result = await runtime.advance_background("thread", "request")
    assert result.status == "failed"
    assert result.events[-2].data["code"] == "model_submission_uncertain"
    assert provider.posts == 1
    await transport.close()


async def test_missing_saved_submission_acknowledgement_is_uncertain(tmp_path, monkeypatch):
    provider = Provider([])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "hello")
    executions = runtime.registry.service.repository.executions
    original = executions.save

    def lose_checkpoint(execution_id, worker_id, context, status):
        if context.get("model_response_id"):
            raise RuntimeError("checkpoint unavailable")
        original(execution_id, worker_id, context, status)

    monkeypatch.setattr(executions, "save", lose_checkpoint)
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await runtime.advance_background("thread", "request")
    await transport.close()
    replacement, transport, _ = worker_at(tmp_path, provider)
    result = await replacement.advance_background("thread", "request")
    assert result.status == "failed"
    assert result.events[-2].data["code"] == "model_submission_uncertain"
    assert provider.posts == 1
    await transport.close()


async def test_transient_collection_error_retries_get_without_resubmitting(tmp_path):
    provider = Provider([503, {"status": "completed", "output": []}])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "hello")
    await runtime.advance_background("thread", "request")
    with pytest.raises(httpx.HTTPStatusError):
        await runtime.advance_background("thread", "request")
    assert (await runtime.advance_background("thread", "request")).status == "completed"
    assert provider.posts == 1
    await transport.close()


@pytest.mark.parametrize("payload,code", [
    (404, "model_response_unavailable"), (410, "model_response_unavailable"),
    ({"status": "failed", "output": [stock_call()]}, "model_response_incomplete"),
    ({"status": "incomplete", "output": [stock_call()]}, "model_response_incomplete"),
    ({"status": "cancelled", "output": [stock_call()]}, "model_response_incomplete"),
    ({"status": "unknown", "output": [stock_call()]}, "model_response_invalid"),
    ({"id": "resp_wrong", "status": "completed", "output": [stock_call()]}, "model_response_invalid"),
])
async def test_unusable_response_never_executes_partial_tools_or_resubmits(tmp_path, payload, code):
    provider = Provider([payload])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "hello")
    await runtime.advance_background("thread", "request")
    result = await runtime.advance_background("thread", "request")
    assert result.status == "failed"
    assert result.events[-2].data["code"] == code
    assert not any(event.kind == "tool_call" for event in result.events)
    assert await runtime.advance_background("thread", "request") == result
    assert provider.posts == 1
    await transport.close()


async def test_approval_between_background_rounds_is_applied_once(tmp_path):
    update = {"type": "function_call", "name": "update_part", "call_id": "update",
        "arguments": json.dumps({"part_id": 1, "fields": {"description": "new"}})}
    provider = Provider([{"status": "completed", "output": [update]},
                         {"status": "completed", "output": []}])
    runtime, transport, _ = worker_at(tmp_path, provider)
    await runtime.registry.execute("add_part", {"part_category": "resistor", "profile": "passive",
        "quantity": 1, "value": "10k"})
    runtime.prepare("thread", "request", "rename")
    await runtime.advance_background("thread", "request")
    await runtime.advance_background("thread", "request")
    pending = await runtime.advance_background("thread", "request")
    assert pending.status == "awaiting_approval"
    decision = ApprovalResponse(next(e.data["request_id"] for e in pending.events
                                    if e.kind == "approval_request"), True)
    await transport.close()
    runtime, transport, _ = worker_at(tmp_path, provider)
    assert (await runtime.advance_background("thread", "request", approval_response=decision)).status == "ready"
    assert (await runtime.advance_background("thread", "request", approval_response=decision)).status == "waiting_model"
    result = await runtime.advance_background("thread", "request")
    assert result.status == "completed"
    assert await runtime.advance_background("thread", "request", approval_response=decision) == result
    assert runtime.registry.service.get(GetPartRequest(1)).description == "new"
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 1
    assert provider.posts == 2
    await transport.close()


async def test_gateway_prepares_and_advances_without_http(tmp_path):
    provider = Provider([{"status": "completed", "output": []}])
    _, transport, _ = worker_at(tmp_path, provider)
    services = create_services(TestRepository(tmp_path / "inventory.db"),
        TestConversations(tmp_path / "events.db"), transport_factory=lambda: transport)
    thread = services.gateway.create_thread()
    assert await services.gateway.prepare(thread, "request", "hello") == "request"
    assert (await services.gateway.advance_background(thread, "request")).status == "waiting_model"
    assert (await services.gateway.advance_background(thread, "request")).status == "completed"
    await services.close()


async def test_waiting_request_blocks_new_intake_and_regular_chat(tmp_path):
    provider = Provider([])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "hello")
    await runtime.advance_background("thread", "request")
    with pytest.raises(ExecutionUnavailable, match="unfinished"):
        runtime.prepare("thread", "different-request", "another message")
    with pytest.raises(ExecutionUnavailable, match="unfinished"):
        await runtime.run("thread", "another message")
    assert provider.posts == 1
    await transport.close()


async def test_collection_checkpoint_failure_retrieves_same_response_again(tmp_path, monkeypatch):
    completed = {"status": "completed", "output": [stock_call()]}
    provider = Provider([completed, completed])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "hello")
    await runtime.advance_background("thread", "request")
    executions = runtime.registry.service.repository.executions
    original = executions.save

    def fail_model_output(execution_id, worker_id, context, status):
        if context.get("pending"):
            raise RuntimeError("checkpoint unavailable")
        original(execution_id, worker_id, context, status)

    monkeypatch.setattr(executions, "save", fail_model_output)
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await runtime.advance_background("thread", "request")
    await transport.close()
    worker, transport, _ = worker_at(tmp_path, provider)
    assert (await worker.advance_background("thread", "request")).status == "ready"
    assert provider.posts == 1
    assert [r.url.path for r in provider.requests if r.method == "GET"] == [
        "/v1/responses/resp_1", "/v1/responses/resp_1"]
    await transport.close()


@pytest.mark.parametrize("accepted,supplied", [
    (ImageInput("image/png", "accepted"), ImageInput("image/png", "different")),
    (ImageInput("image/png", "accepted"), ImageInput("image/jpeg", "accepted")),
    (None, ImageInput("image/png", "unexpected")),
])
async def test_worker_cannot_substitute_photo_after_intake(tmp_path, accepted, supplied):
    provider = Provider([])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "identify", image=accepted)
    result = await runtime.advance_background("thread", "request", image=supplied)
    assert result.status == "failed"
    assert result.events[-2].data["code"] == "image_identity_mismatch"
    assert not provider.requests
    await transport.close()


async def test_unsent_photo_request_requires_resubmission(tmp_path):
    provider = Provider([])
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.prepare("thread", "request", "identify", image=ImageInput("image/png", "private"))
    result = await runtime.advance_background("thread", "request")
    assert result.status == "failed"
    assert result.events[-2].data["code"] == "image_resubmission_required"
    assert not provider.requests
    await transport.close()
