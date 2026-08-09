from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agent_runtime import CodexAppServerTransport, ImageInput, ModelTurn
from agent_runtime.runtime import ModelRequest


class _Stdin:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    def write(self, payload: bytes) -> None:
        self.writes.append(json.loads(payload))

    async def drain(self) -> None:
        return None


class _Stdout:
    def __init__(self, messages: list[dict]) -> None:
        self.lines = [json.dumps(message).encode() + b"\n" for message in messages]

    async def readline(self) -> bytes:
        return self.lines.pop(0)


class _Process:
    def __init__(self, messages: list[dict]) -> None:
        self.stdin = _Stdin()
        self.stdout = _Stdout(messages)
        self.stderr = None
        self.returncode = None


def _request(thread_id: str = "parts-thread") -> ModelRequest:
    return ModelRequest("system instructions", "hello", ImageInput("image/png", "AA=="), (), (), thread_id=thread_id)


@pytest.mark.asyncio
async def test_codex_transport_uses_json_rpc_lifecycle_and_streamed_notifications():
    process = _Process([
        {"jsonrpc": "2.0", "id": "1", "result": {"codexHome": "/tmp"}},
        {"jsonrpc": "2.0", "id": "2", "result": {"thread": {"id": "codex-1"}}},
        {"jsonrpc": "2.0", "id": "3", "result": {"turn": {"id": "turn-1"}}},
        {"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": "codex-1", "delta": "hello"}},
        {"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": "codex-1", "delta": " world"}},
        {"jsonrpc": "2.0", "method": "item/started", "params": {"item": {"type": "mcpToolCall", "id": "tool-1", "tool": "search_parts", "arguments": {"filters": {}}}}},
        {"jsonrpc": "2.0", "method": "item/completed", "params": {"item": {"type": "mcpToolCall", "id": "tool-1", "tool": "search_parts", "arguments": {"filters": {}}, "error": None, "result": {"parts": []}}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "codex-1", "turn": {"id": "turn-1", "status": "completed"}}},
    ])
    transport = CodexAppServerTransport(command="codex app-server --stdio")
    transport._ensure_process = AsyncMock(return_value=process)

    result = await transport.complete(_request())

    assert result.text == "hello world"
    assert result.protocol_events == (
        ("tool_call", {"call_id": "tool-1", "name": "search_parts", "arguments": {"filters": {}}}),
        ("tool_result", {"call_id": "tool-1", "name": "search_parts", "result": {"parts": []}}),
    )
    assert [message["method"] for message in process.stdin.writes] == ["initialize", "initialized", "thread/start", "turn/start"]
    assert process.stdin.writes[0]["params"]["clientInfo"]["name"] == "parts-bin"
    assert process.stdin.writes[2]["params"]["baseInstructions"] == "system instructions"
    assert process.stdin.writes[3]["params"]["threadId"] == "codex-1"
    assert process.stdin.writes[3]["params"]["input"][-1] == {"type": "image", "url": "data:image/png;base64,AA=="}
    assert process.stdin.writes[3]["params"]["approvalPolicy"] == "never"


@pytest.mark.asyncio
async def test_codex_transport_reuses_initialized_process_thread():
    process = _Process([
        {"jsonrpc": "2.0", "id": "1", "result": {"codexHome": "/tmp"}},
        {"jsonrpc": "2.0", "id": "2", "result": {"thread": {"id": "codex-1"}}},
        {"jsonrpc": "2.0", "id": "3", "result": {"turn": {"id": "turn-1"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "codex-1", "turn": {"id": "turn-1", "text": "one"}}},
        {"jsonrpc": "2.0", "id": "4", "result": {"turn": {"id": "turn-2"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "codex-1", "turn": {"id": "turn-2", "text": "two"}}},
    ])
    transport = CodexAppServerTransport(command="codex app-server --stdio")
    transport._ensure_process = AsyncMock(return_value=process)

    assert (await transport.complete(_request())).text == "one"
    assert (await transport.complete(_request())).text == "two"
    assert [message["method"] for message in process.stdin.writes] == ["initialize", "initialized", "thread/start", "turn/start", "turn/start"]
