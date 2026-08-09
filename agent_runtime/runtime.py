"""Normalized bounded agent loops and the Codex/OpenAI/local implementations.

The only non-native local tool format is a complete JSON object:
``{"type":"parts_bin_tool_call","name":"search_parts","arguments":{...}}``.
No prose or extra keys are permitted in that envelope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

from tools import PartsBinToolRegistry, ToolExecutionContext

from .approval import ApprovalEngine
from .models import ApprovalResponse, ConversationEvent, ImageInput, ModelTurn, RuntimeName, RuntimeResult, ToolCall
from .store import ConversationStore
from .telemetry import AgentTelemetry, _domain_outcome

SYSTEM_INSTRUCTIONS = """You are the Parts Bin assistant. Inventory facts must be discovered with Parts Bin tools; never assume or list unseen inventory. Use tools for every inventory fact and mutation."""
JSON_TOOL_ENVELOPE = '{"type":"parts_bin_tool_call","name":"<registered tool name>","arguments":{}}'


@dataclass(frozen=True)
class ModelRequest:
    system: str
    user_text: str
    image: ImageInput | None
    tools: tuple[dict[str, Any], ...]
    exchanges: tuple[dict[str, Any], ...]
    json_tool_envelope: bool = False
    thread_id: str | None = None
    history: tuple[dict[str, str], ...] = ()


class ModelTransport(Protocol):
    async def complete(self, request: ModelRequest) -> ModelTurn: ...


class MCPClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any], *, approval: Any | None = None) -> dict[str, Any]: ...


class DirectToolExecutor:
    def __init__(self, registry: PartsBinToolRegistry):
        self.registry = registry

    async def execute(self, name: str, arguments: dict[str, Any], approval: Any | None) -> dict[str, Any]:
        return await self.registry.execute(name, arguments, context=ToolExecutionContext(approval=approval))


class MCPToolExecutor:
    """Codex's tool path. It never calls the registry directly."""

    def __init__(self, client: MCPClient):
        self.client = client

    async def execute(self, name: str, arguments: dict[str, Any], approval: Any | None) -> dict[str, Any]:
        return await self.client.call_tool(name, arguments, approval=approval)


@dataclass
class _TurnState:
    thread_id: str
    user_text: str
    image: ImageInput | None
    exchanges: list[dict[str, Any]] = field(default_factory=list)
    history: tuple[dict[str, str], ...] = ()


class AgentRuntime:
    """One event and approval contract around an implementation-specific transport."""

    runtime: RuntimeName

    def __init__(self, *, runtime: RuntimeName, registry: PartsBinToolRegistry, store: ConversationStore,
                 approvals: ApprovalEngine, executor: DirectToolExecutor | MCPToolExecutor,
                 max_tool_turns: int = 8, telemetry: AgentTelemetry | None = None) -> None:
        if max_tool_turns < 1:
            raise ValueError("max_tool_turns must be positive")
        self.runtime = runtime
        self.registry = registry
        self.store = store
        self.approvals = approvals
        # The registry remains the enforcement point; this is its one host
        # approval decision source for every direct or MCP-projected call.
        self.registry.approval_checker = approvals.checker
        self.executor = executor
        self.max_tool_turns = max_tool_turns
        self.telemetry = telemetry or AgentTelemetry()

    async def run(self, thread_id: str, user_text: str, *, image: ImageInput | None = None,
                  approval_response: ApprovalResponse | None = None) -> RuntimeResult:
        self.store.create_thread(thread_id, self.runtime)
        emitted: list[ConversationEvent] = []
        started = perf_counter()
        domain_outcome: str | None = None

        def emit(kind: str, data: dict[str, Any]) -> None:
            emitted.append(self.store.append(ConversationEvent(kind, thread_id, self.runtime, data)))

        def finish(status: str) -> RuntimeResult:
            self.telemetry.turn_finished(thread_id, self.runtime, latency_ms=(perf_counter() - started) * 1000,
                                         status=status, domain_outcome=domain_outcome)
            return RuntimeResult(tuple(emitted), status)  # type: ignore[arg-type]

        if approval_response is not None:
            try:
                request = self.approvals.decide(thread_id, approval_response.request_id, approval_response.approved)
            except ValueError as exc:
                emit("error", {"code": "invalid_approval_response", "message": str(exc)})
                emit("completed", {"status": "failed"})
                return finish("failed")
            emit("approval_decision", {"request_id": request.request_id, "tool": request.tool_name,
                                       "approved": approval_response.approved})
            self.telemetry.approval_decided(thread_id, self.runtime, request.tool_name, approval_response.approved)

        history = tuple(
            {"role": "user" if event.kind == "user_message" else "assistant", "text": str(event.data.get("text", ""))}
            for event in self.store.events(thread_id)
            if event.kind in {"user_message", "assistant_text"} and event.data.get("text")
        )
        emit("user_message", {"text": user_text, "image": None if image is None else {"media_type": image.media_type}})
        state = _TurnState(thread_id, user_text, image, history=history)
        if approval_response is not None and not approval_response.approved:
            state.exchanges.append({"type": "approval_denied", "request_id": approval_response.request_id})

        for tool_turn in range(self.max_tool_turns + 1):
            if tool_turn == self.max_tool_turns:
                emit("error", {"code": "tool_loop_limit", "message": f"Tool loop exceeded {self.max_tool_turns} turns"})
                emit("completed", {"status": "failed"})
                self.telemetry.loop_limit(thread_id, self.runtime, self.max_tool_turns)
                return finish("failed")
            try:
                turn = await self._complete(state)
            except Exception:
                self.telemetry.runtime_failure(thread_id, self.runtime, "model_transport_failed")
                raise
            for kind, data in turn.protocol_events:
                emit(kind, data)
            if turn.text:
                emit("assistant_text", {"text": turn.text})
            if not turn.tool_calls:
                emit("completed", {"status": "completed"})
                return finish("completed")
            for call in turn.tool_calls:
                emit("tool_call", {"call_id": call.call_id, "name": call.name, "arguments": call.arguments})
                self.telemetry.tool_started(thread_id, self.runtime, call.name, call.arguments)
                receipt = self.approvals.receipt_for(thread_id, call.name, call.arguments)
                tool_started = perf_counter()
                try:
                    result = await self.executor.execute(call.name, call.arguments, receipt)
                except Exception:
                    self.telemetry.runtime_failure(thread_id, self.runtime, "tool_executor_failed")
                    raise
                self.telemetry.tool_finished(thread_id, self.runtime, call.name, call.arguments,
                                             latency_ms=(perf_counter() - tool_started) * 1000, result=result)
                if result.get("error", {}).get("code") == "approval_required":
                    request = self.approvals.request(thread_id, call.name, call.arguments)
                    emit("approval_request", {"request_id": request.request_id, "tool": call.name,
                                              "target": call.arguments.get("part_id", call.arguments.get("part_ids", "selection")),
                                              "effect": _approval_effect(call.name, call.arguments), "arguments": call.arguments})
                    emit("completed", {"status": "awaiting_approval"})
                    return finish("awaiting_approval")
                emit("tool_result", {"call_id": call.call_id, "name": call.name, "result": result})
                if result.get("ok"):
                    domain_outcome = _domain_outcome(call.name, result)
                state.exchanges.append({"type": "tool_result", "call_id": call.call_id,
                                        "name": call.name, "result": result})
        raise AssertionError("unreachable")

    async def _complete(self, state: _TurnState) -> ModelTurn:
        raise NotImplementedError


class OpenAIResponsesRuntime(AgentRuntime):
    """OpenAI Responses API adapter using registry schemas as native functions."""

    def __init__(self, transport: ModelTransport, **kwargs: Any) -> None:
        super().__init__(runtime="openai", executor=DirectToolExecutor(kwargs["registry"]), **kwargs)
        self.transport = transport

    async def _complete(self, state: _TurnState) -> ModelTurn:
        return await self.transport.complete(ModelRequest(SYSTEM_INSTRUCTIONS, state.user_text, state.image,
            tuple(_responses_tool(tool) for tool in self.registry.list_tools()), tuple(state.exchanges),
            thread_id=state.thread_id, history=state.history))


class LocalOpenAICompatibleRuntime(AgentRuntime):
    """Local adapter with native calls or the documented JSON tool envelope."""

    def __init__(self, transport: ModelTransport, *, supports_native_tools: bool, **kwargs: Any) -> None:
        super().__init__(runtime="local", executor=DirectToolExecutor(kwargs["registry"]), **kwargs)
        self.transport = transport
        self.supports_native_tools = supports_native_tools

    async def _complete(self, state: _TurnState) -> ModelTurn:
        request = ModelRequest(SYSTEM_INSTRUCTIONS + ("\nWhen a tool is needed, reply with exactly " + JSON_TOOL_ENVELOPE if not self.supports_native_tools else ""),
            state.user_text, state.image, tuple(_chat_tool(tool) for tool in self.registry.list_tools()) if self.supports_native_tools else (),
            tuple(state.exchanges), json_tool_envelope=not self.supports_native_tools, thread_id=state.thread_id)
        turn = await self.transport.complete(request)
        return _parse_local_envelope(turn) if not self.supports_native_tools else turn


class CodexAppServerRuntime(AgentRuntime):
    """Codex app-server adapter. All tool execution is projected through MCP."""

    def __init__(self, transport: ModelTransport, *, mcp_client: MCPClient, **kwargs: Any) -> None:
        super().__init__(runtime="codex", executor=MCPToolExecutor(mcp_client), **kwargs)
        self.transport = transport

    async def _complete(self, state: _TurnState) -> ModelTurn:
        # App-server transports receive the same registry projection as their MCP configuration.
        return await self.transport.complete(ModelRequest(SYSTEM_INSTRUCTIONS, state.user_text, state.image,
            tuple(self.registry.list_tools()), tuple(state.exchanges), thread_id=state.thread_id, history=state.history))


def _responses_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "name": tool["name"], "description": tool["description"], "parameters": tool["inputSchema"], "strict": True}


def _chat_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["inputSchema"], "strict": True}}


def _parse_local_envelope(turn: ModelTurn) -> ModelTurn:
    if turn.tool_calls or not turn.text.lstrip().startswith("{"):
        return turn
    try:
        envelope = json.loads(turn.text)
    except json.JSONDecodeError:
        # A would-be JSON response is never treated as a natural-language
        # answer: return the registry's stable validation error to the model.
        return ModelTurn(tool_calls=(ToolCall("__invalid_json_tool_envelope__", {}, "invalid-envelope"),))
    if not isinstance(envelope, dict) or envelope.get("type") != "parts_bin_tool_call":
        return turn
    if set(envelope) != {"type", "name", "arguments"} or not isinstance(envelope["name"], str) or not isinstance(envelope["arguments"], dict):
        return ModelTurn(tool_calls=(ToolCall("__invalid_json_tool_envelope__", {}, "invalid-envelope"),))
    return ModelTurn(tool_calls=(ToolCall(envelope["name"], envelope["arguments"], "json-envelope"),))


def _approval_effect(tool_name: str, arguments: dict[str, Any]) -> str:
    fields = arguments.get("fields") or arguments.get("updates")
    if isinstance(fields, dict):
        return f"{tool_name}: change {', '.join(sorted(fields))}"
    if isinstance(fields, list):
        return f"{tool_name}: reject {', '.join(fields)}"
    return tool_name.replace("_", " ")
