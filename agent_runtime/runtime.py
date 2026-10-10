"""Bounded OpenAI agent loop with server-owned tools and approvals."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Protocol

from tools import PartsBinToolRegistry, ToolExecutionContext

from .approval import ApprovalEngine
from .models import ApprovalResponse, ConversationEvent, ImageInput, ModelTurn, RuntimeResult
from .store import ConversationStore
from .telemetry import AgentTelemetry, _domain_outcome

SYSTEM_INSTRUCTIONS = """You are the Parts Bin assistant. Inventory facts must be discovered with Parts Bin tools; never assume or list unseen inventory. Use tools for every inventory fact and mutation.
Use electronics knowledge to identify named components, distinguishing it from stored inventory facts. Add common identifiable ICs with a functional category and meaningful description, rather than a generic IC label. Do not invent manufacturer, package, or electrical specifications from an ambiguous base part number. Preserve user-provided quantity and package.
Search before adding stock. A matching base part number does not establish that different package variants are the same stock. Clarify before merging uncertain variants. 'I have 10, not 100' sets quantity to 10; it is not an increment.
Adding an identified IC stages supplier details automatically; inspect the enrichment outcome. Use lookup_part_specs to retry unavailable lookups or enrich existing parts. Explain lookup failures or pending reviews; staged proposals are not committed facts. Submit update_part or apply_review for corrections and accepted enrichment so the server presents approval controls. Do not ask for approval only in prose or claim tools cannot be approved in this session.
"""
JSON_TOOL_ENVELOPE = '{"type":"parts_bin_tool_call","name":"<registered tool name>","arguments":{}}'


@dataclass(frozen=True)
class ModelRequest:
    system: str
    user_text: str
    image: ImageInput | None
    tools: tuple[dict[str, Any], ...]
    exchanges: tuple[dict[str, Any], ...]
    thread_id: str | None = None
    history: tuple[dict[str, str], ...] = ()


class ModelTransport(Protocol):
    async def complete(self, request: ModelRequest) -> ModelTurn: ...


@dataclass
class _TurnState:
    thread_id: str
    user_text: str
    image: ImageInput | None
    exchanges: list[dict[str, Any]] = field(default_factory=list)
    history: tuple[dict[str, str], ...] = ()


class OpenAIResponsesRuntime:
    """Execute native function calls through the typed registry."""

    runtime = "openai"

    def __init__(self, transport: ModelTransport, *, registry: PartsBinToolRegistry,
                 store: ConversationStore, approvals: ApprovalEngine,
                 max_tool_turns: int = 8, telemetry: AgentTelemetry | None = None) -> None:
        if max_tool_turns < 1:
            raise ValueError("max_tool_turns must be positive")
        self.transport = transport
        self.registry = registry
        self.store = store
        self.approvals = approvals
        self.registry.approval_checker = approvals.checker
        self.max_tool_turns = max_tool_turns
        self.telemetry = telemetry or AgentTelemetry()

    async def run(self, thread_id: str, user_text: str, *, image: ImageInput | None = None,
                  approval_response: ApprovalResponse | None = None,
                  on_event: Callable[[ConversationEvent], None] | None = None) -> RuntimeResult:
        self.store.create_thread(thread_id, self.runtime)
        emitted: list[ConversationEvent] = []
        started = perf_counter()
        domain_outcome: str | None = None

        def emit(kind: str, data: dict[str, Any]) -> None:
            event = self.store.append(ConversationEvent(kind, thread_id, self.runtime, data))
            emitted.append(event)
            if on_event is not None:
                on_event(event)

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

        # Resume the exact approved operation without model reconstruction.
        if approval_response is not None and approval_response.approved:
            receipt = self.approvals.receipt_for(thread_id, request.tool_name, request.arguments)
            emit("tool_call", {"call_id": request.request_id, "name": request.tool_name, "arguments": request.arguments})
            self.telemetry.tool_started(thread_id, self.runtime, request.tool_name, request.arguments)
            tool_started = perf_counter()
            result = await self.registry.execute(request.tool_name, request.arguments, context=ToolExecutionContext(approval=receipt))
            self.telemetry.tool_finished(thread_id, self.runtime, request.tool_name, request.arguments,
                                         latency_ms=(perf_counter() - tool_started) * 1000, result=result)
            emit("tool_result", {"call_id": request.request_id, "name": request.tool_name, "arguments": request.arguments, "result": result})
            state.exchanges.append({"type": "tool_result", "call_id": request.request_id,
                                    "name": request.tool_name, "arguments": request.arguments, "result": result})
            if result.get("ok"):
                domain_outcome = _domain_outcome(request.tool_name, result)

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
            if turn.response_output:
                # Keep provider reasoning/function items for the next request in this
                # turn; these are not user-visible events or telemetry.
                state.exchanges.append({"type": "model_output", "items": turn.response_output})
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
                    result = await self.registry.execute(call.name, call.arguments, context=ToolExecutionContext(approval=receipt))
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
                                        "name": call.name, "arguments": call.arguments, "result": result})
        raise AssertionError("unreachable")

    async def _complete(self, state: _TurnState) -> ModelTurn:
        tools = tuple({"type": "function", "name": tool["name"],
                       "description": tool["description"], "parameters": tool["inputSchema"],
                       # Patch-style tools intentionally distinguish omitted fields from null.
                       # The registry validates inputs; strict mode would require every field.
                       "strict": False} for tool in self.registry.list_tools())
        return await self.transport.complete(ModelRequest(
            SYSTEM_INSTRUCTIONS, state.user_text, state.image, tools,
            tuple(state.exchanges), thread_id=state.thread_id, history=state.history,
        ))


def _approval_effect(tool_name: str, arguments: dict[str, Any]) -> str:
    fields = arguments.get("fields") or arguments.get("updates")
    if isinstance(fields, dict):
        return f"{tool_name}: change {', '.join(sorted(fields))}"
    if isinstance(fields, list):
        return f"{tool_name}: reject {', '.join(fields)}"
    return tool_name.replace("_", " ")
