"""Exercise the actual Responses request boundary without spending API credits."""

import json

import httpx
import pytest
from db.repository import SQLitePartsBinRepository

from agent_runtime import ApprovalEngine, ConversationStore, ImageInput, OpenAIResponsesRuntime, OpenAIResponsesTransport
from agent_runtime.runtime import ModelRequest
from domain import PartsBinService
from tools import PartsBinToolRegistry


async def test_tool_continuation_preserves_response_items_without_duplicate_calls(tmp_path):
    requests = []
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-test-content"}
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "search_parts", "arguments": '{"filters":{"part_number":"PBSS5350T"}}'}

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-only"
        if len(requests) == 1:
            return httpx.Response(200, json={"output": [reasoning, call]})
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "No matching inventory."}]}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    transport = OpenAIResponsesTransport(api_key="test-only", model="test-model", client=client)
    store = ConversationStore(tmp_path / "parts.db")
    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    runtime = OpenAIResponsesRuntime(transport, registry=PartsBinToolRegistry(PartsBinService(repository)),
                                    store=store, approvals=ApprovalEngine(repository))
    try:
        result = await runtime.run("thread", "Find this part", image=ImageInput("image/png", "AA=="))
    finally:
        await transport.close()
    assert result.status == "completed"
    assert len(requests) == 2
    first, second = requests
    assert first["store"] is False
    assert first["parallel_tool_calls"] is False
    assert first["input"][0]["content"][1]["image_url"] == "data:image/png;base64,AA=="
    assert second["input"][1:3] == [reasoning, call]
    assert sum(item.get("type") == "function_call" for item in second["input"]) == 1
    assert second["input"][3]["call_id"] == "call_1"
    assert json.loads(second["input"][3]["output"])["result"]["parts"] == []
    assert "opaque-test-content" not in repr(store.events("thread"))
    # Optional patch fields must not be normalized to required/null by the API.
    update_tool = next(tool for tool in first["tools"] if tool["name"] == "update_part")
    assert update_tool["strict"] is False
    assert client.is_closed


async def test_approval_outcomes_reach_model_without_reconstructing_mutation():
    captured = []
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"output": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        transport = OpenAIResponsesTransport(api_key="test-only", model="test-model", client=client)
        await transport.complete(ModelRequest("instructions", "", None, (), (
            {"type": "tool_result", "call_id": "approved", "name": "update_part", "arguments": {"part_id": 1, "fields": {"quantity": 2}}, "result": {"ok": True}},
        )))
        await transport.complete(ModelRequest("instructions", "", None, (), ({"type": "approval_denied", "request_id": "denied"},)))
    assert [item.get("type") for item in captured[0]["input"][1:]] == ["function_call", "function_call_output"]
    assert "declined" in captured[1]["input"][-1]["content"]


async def test_provider_error_is_not_a_successful_empty_answer():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401))) as client:
        transport = OpenAIResponsesTransport(api_key="test-only", model="test-model", client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await transport.complete(ModelRequest("instructions", "hello", None, (), ()))
