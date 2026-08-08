"""Concrete transports for the three explicitly selected agent runtimes."""

from __future__ import annotations

import json
import asyncio
import shlex
from typing import Any

import httpx

from .models import ModelTurn, ToolCall
from .runtime import ModelRequest


class CodexAppServerTransport:
    """JSON-RPC client for the Codex app-server stdio protocol."""

    def __init__(self, *, command: str) -> None:
        self.command = command
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._initialized = False
        self._threads: dict[str, str] = {}

    async def complete(self, request: ModelRequest) -> ModelTurn:
        async with self._lock:
            process = await self._ensure_process()
            assert process.stdin is not None and process.stdout is not None
            await self._initialize(process)
            codex_thread = await self._thread_for(process, request)
            turn_request = await self._send(process, "turn/start", {
                "threadId": codex_thread, "input": self._input(request),
            })
            turn_id: str | None = None
            text: list[str] = []
            protocol_events: list[tuple[str, dict[str, Any]]] = []
            while True:
                message = await self._read_message(process)
                if message.get("id") == turn_request:
                    result = message.get("result") or {}
                    turn = result.get("turn") or result
                    turn_id = turn.get("id") if isinstance(turn, dict) else None
                    continue
                if message.get("method") == "item/agentMessage/delta":
                    params = message.get("params") or {}
                    if params.get("threadId") == codex_thread:
                        text.append(str(params.get("delta", "")))
                    continue
                if message.get("method") == "item/completed":
                    item = (message.get("params") or {}).get("item") or {}
                    if item.get("type") == "mcpToolCall":
                        protocol_events.append(("tool_result", {
                            "call_id": str(item.get("id", "")),
                            "name": str(item.get("tool", "")),
                            "result": item.get("result") if item.get("error") is None else {"error": item.get("error")},
                        }))
                    if item.get("type") == "agentMessage" and not text:
                        text.append(str(item.get("text", "")))
                    continue
                if message.get("method") == "item/started":
                    item = (message.get("params") or {}).get("item") or {}
                    if item.get("type") == "mcpToolCall":
                        protocol_events.append(("tool_call", {
                            "call_id": str(item.get("id", "")),
                            "name": str(item.get("tool", "")),
                            "arguments": item.get("arguments") or {},
                        }))
                    continue
                if message.get("method") == "turn/completed":
                    params = message.get("params") or {}
                    if params.get("threadId") == codex_thread and (turn_id is None or (params.get("turn") or {}).get("id") == turn_id):
                        if not text:
                            text.append(str((params.get("turn") or {}).get("text", "")))
                        return ModelTurn("".join(text), protocol_events=tuple(protocol_events))
                if "error" in message and message.get("id") == turn_request:
                    raise RuntimeError(str(message["error"].get("message", "Codex app-server request failed")))

    async def close(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()
        self._process = None
        self._initialized = False
        self._threads.clear()

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        if not self.command.strip():
            raise RuntimeError("Codex runtime is not configured (agent.codex.command)")
        self._process = await asyncio.create_subprocess_exec(
            *shlex.split(self.command), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        return self._process

    async def _initialize(self, process: asyncio.subprocess.Process) -> None:
        if self._initialized:
            return
        request_id = await self._send(process, "initialize", {
            "clientInfo": {"name": "parts-bin", "title": "Parts Bin", "version": "0.1.0"},
            "capabilities": {"experimentalApi": False},
        })
        while True:
            message = await self._read_message(process)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"].get("message", "Codex initialization failed")))
                break
        await self._notify(process, "initialized", {})
        self._initialized = True

    async def _thread_for(self, process: asyncio.subprocess.Process, request: ModelRequest) -> str:
        key = request.thread_id or "default"
        if key in self._threads:
            return self._threads[key]
        request_id = await self._send(process, "thread/start", {
            "baseInstructions": request.system, "ephemeral": True,
        })
        while True:
            message = await self._read_message(process)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(str(message["error"].get("message", "Codex thread start failed")))
            result = message.get("result") or {}
            thread = result.get("thread") or result
            codex_thread = str(thread.get("id", ""))
            if not codex_thread:
                raise RuntimeError("Codex app-server returned no thread id")
            self._threads[key] = codex_thread
            return codex_thread

    @staticmethod
    def _input(request: ModelRequest) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = [{"type": "text", "text": request.user_text}]
        if request.image is not None:
            items.append({"type": "image", "url": f"data:{request.image.media_type};base64,{request.image.data_base64}"})
        return items

    async def _send(self, process: asyncio.subprocess.Process, method: str, params: dict[str, Any]) -> str:
        self._next_id += 1
        request_id = str(self._next_id)
        await self._write(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return request_id

    async def _notify(self, process: asyncio.subprocess.Process, method: str, params: dict[str, Any]) -> None:
        await self._write(process, {"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        await process.stdin.drain()

    async def _read_message(self, process: asyncio.subprocess.Process) -> dict[str, Any]:
        assert process.stdout is not None
        line = await process.stdout.readline()
        if not line:
            stderr = b"" if process.stderr is None else await process.stderr.read()
            raise RuntimeError(f"Codex app-server stopped unexpectedly: {stderr.decode(errors='replace').strip()}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex app-server returned invalid JSON-RPC") from exc


class OpenAIResponsesTransport:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", client: httpx.AsyncClient | None = None):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=60.0)

    async def complete(self, request: ModelRequest) -> ModelTurn:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": request.user_text}]
        if request.image is not None:
            content.append({"type": "input_image", "image_url": f"data:{request.image.media_type};base64,{request.image.data_base64}"})
        input_items: list[dict[str, Any]] = [{"role": "user", "content": content}]
        for exchange in request.exchanges:
            if exchange["type"] == "tool_result":
                input_items.append({"type": "function_call_output", "call_id": exchange["call_id"],
                                    "output": json.dumps(exchange["result"], separators=(",", ":"))})
        response = await self.client.post(f"{self.base_url}/responses", headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "instructions": request.system, "input": input_items, "tools": list(request.tools), "tool_choice": "auto"})
        response.raise_for_status()
        payload = response.json()
        text: list[str] = []
        calls: list[ToolCall] = []
        for item in payload.get("output", []):
            if item.get("type") == "function_call":
                try:
                    arguments = json.loads(item.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                calls.append(ToolCall(item.get("name", ""), arguments, item.get("call_id", "")))
            elif item.get("type") == "message":
                text.extend(part.get("text", "") for part in item.get("content", []) if part.get("type") == "output_text")
        return ModelTurn("".join(text), tuple(calls))


class LocalOpenAICompatibleTransport:
    def __init__(self, *, model: str, base_url: str, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.model, self.base_url, self.api_key = model, base_url.rstrip("/"), api_key
        self.client = client or httpx.AsyncClient(timeout=60.0)

    async def complete(self, request: ModelRequest) -> ModelTurn:
        user_content: Any = request.user_text
        if request.image is not None:
            user_content = [{"type": "text", "text": request.user_text}, {"type": "image_url", "image_url": {"url": f"data:{request.image.media_type};base64,{request.image.data_base64}"}}]
        messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}, {"role": "user", "content": user_content}]
        for exchange in request.exchanges:
            if exchange["type"] == "tool_result":
                messages.append({"role": "tool", "tool_call_id": exchange["call_id"],
                                 "content": json.dumps(exchange["result"], separators=(",", ":"))})
        headers = {} if not self.api_key else {"Authorization": f"Bearer {self.api_key}"}
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if request.tools:
            body.update({"tools": list(request.tools), "tool_choice": "auto"})
        response = await self.client.post(f"{self.base_url}/" + "chat/completions", headers=headers, json=body)
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(function.get("name", ""), arguments, call.get("id", "")))
        return ModelTurn(message.get("content") or "", tuple(calls))
