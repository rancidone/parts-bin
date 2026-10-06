from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_runtime import CodexAppServerTransport, ImageInput, ModelTurn
from agent_runtime.runtime import ModelRequest
from agent_runtime.transports import CodexExecTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "codex-session"])
async def test_codex_exec_photo_keeps_prompt_outside_image_arguments(monkeypatch, session_id):
    async def spawn(*args, **kwargs):
        separator = args.index("--")
        image_option = args.index("--image")
        assert args[image_option + 2] == "--"
        assert len(args[separator + 1:]) == 1
        assert "User request:\nhello" in args[-1]
        image_path = Path(args[image_option + 1])
        assert image_path.read_bytes() == b"\x00"
        if session_id:
            assert args[:4] == ("codex", "exec", "resume", session_id)
        spawn.image_path = image_path
        return SimpleNamespace(
            stdout=SimpleNamespace(readline=AsyncMock(side_effect=[
                b'{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}\n', b"",
            ])),
            stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
            wait=AsyncMock(return_value=0), returncode=0,
        )

    monkeypatch.setattr("agent_runtime.transports.asyncio.create_subprocess_exec", spawn)
    transport = CodexExecTransport(command="codex exec", model="test-model",
        get_session=lambda _: session_id, set_session=lambda *_: None)
    assert (await transport.complete(_request())).text == "OK"
    assert not spawn.image_path.exists()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [True, False])
async def test_codex_exec_routes_mcp_approval_failure_to_gateway(monkeypatch, structured):
    arguments = {"part_id": 1, "fields": {"quantity": 10, "package": "DIP", "part_category": "operational amplifier"}}
    outcome = {"ok": False, "error": {"code": "approval_required"}}
    result = {"structuredContent": outcome} if structured else {"content": [{"type": "text", "text": json.dumps(outcome)}]}
    events = [
        {"type": "item.completed", "item": {"id": "u", "type": "mcp_tool_call", "tool": "update_part", "arguments": arguments, "result": result}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Cannot approve"}},
    ]
    async def spawn(*args, **kwargs):
        return SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(side_effect=[*(json.dumps(e).encode() + b"\n" for e in events), b""])),
            stderr=SimpleNamespace(read=AsyncMock(return_value=b"")), wait=AsyncMock(return_value=0), returncode=0)
    monkeypatch.setattr("agent_runtime.transports.asyncio.create_subprocess_exec", spawn)
    transport = CodexExecTransport(command="codex exec", get_session=lambda _: None, set_session=lambda *_: None)
    turn = await transport.complete(ModelRequest("system", "correct", None, (), (), thread_id="t"))
    assert turn.text == ""
    assert turn.tool_calls[0].arguments == arguments
    assert turn.tool_calls[0].name == "update_part"
