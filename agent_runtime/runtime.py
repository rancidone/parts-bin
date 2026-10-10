"""Bounded OpenAI agent loop with server-owned tools and approvals."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4

from tools import PartsBinToolRegistry, ToolExecutionContext

from .approval import ApprovalEngine
from .models import ApprovalResponse, ConversationEvent, ImageInput, ModelTurn, RuntimeResult
from .store import ConversationRepository
from .telemetry import AgentTelemetry, _domain_outcome

SYSTEM_INSTRUCTIONS = """You are the Parts Bin assistant. Inventory facts must be discovered with Parts Bin tools; never assume or list unseen inventory. Use tools for every inventory fact and mutation.
Use electronics knowledge to identify named components, distinguishing it from stored inventory facts. Add common identifiable ICs with a functional category and meaningful description, rather than a generic IC label. Do not invent manufacturer, package, or electrical specifications from an ambiguous base part number. Preserve user-provided quantity and package.
For inventory lookup, translate nominal values and required stock into search_parts filters and minimum_quantity. Preserve exact ordering-code suffixes. If package or units are ambiguous, ask a targeted question; do not infer electrical suitability from nominal value, description, or pending enrichment. For electrical requirements, call get_specification_contract for the category, then query supported requirements with explicit units, basis, and conditions. Unknown fields and ambiguous categories need clarification. Compare source-backed matches separately from incomplete candidates; absolute maxima and thresholds do not establish application suitability. User assertions may be staged with stage_specification_review and accepted through apply_specification_review, but remain assertions. Source-backed electrical facts require reviewed source ingestion; lookup_part_specs currently retrieves base metadata only.
For descriptions, manufacturer names, partial part numbers, or recorded markings, use search_candidates with a literal fragment. These results identify candidates only. Preserve exact returned ordering codes and suffixes, report stock per record, and clarify ambiguous identity before adding or merging stock. A marking absent from committed descriptions and part numbers cannot be discovered by this tool. Use search_parts for exact ordering-code lookup.
Search pages: use the returned next_offset with unchanged search arguments and limit to retrieve remaining results when completeness is needed; stop when next_offset is null. Specification matches and incomplete candidates paginate separately using the same offset. Never claim a truncated page is the entire inventory. If stock or accepted facts change during pagination, restart the search.
Search before adding stock. A matching base part number does not establish that different package variants are the same stock. Clarify before merging uncertain variants. 'I have 10, not 100' sets quantity to 10; it is not an increment.
Adding an identified IC stages supplier details automatically; inspect the enrichment outcome. Use lookup_part_specs to retry unavailable lookups or enrich existing parts. Explain lookup failures or pending reviews; staged proposals are not committed facts. Submit update_part or apply_review for corrections and accepted enrichment so the server presents approval controls. Do not ask for approval only in prose or claim tools cannot be approved in this session.
If retrieval was interrupted, report it and ask the user for a new lookup request. Do not automatically retry that paid stage within the recovering execution.
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
    round: int = 0
    pending: dict[str, Any] | None = None
    call_index: int = 0
    approval_id: str | None = None
    had_image: bool = False
    error: dict[str, Any] | None = None

    def checkpoint(self) -> dict[str, Any]:
        # Image bytes never enter durable execution state.
        return {"user_text": self.user_text, "exchanges": self.exchanges, "history": self.history,
                "round": self.round, "pending": self.pending, "call_index": self.call_index,
                "approval_id": self.approval_id, "had_image": self.had_image, "error": self.error}

    @classmethod
    def restore(cls, thread_id: str, context: dict[str, Any], image: ImageInput | None):
        return cls(thread_id, context["user_text"], image, list(context["exchanges"]),
                   tuple(context["history"]), context["round"], context["pending"],
                   context["call_index"], context["approval_id"], context["had_image"], context["error"])


class OpenAIResponsesRuntime:
    """Execute native function calls through the typed registry."""

    runtime = "openai"

    def __init__(self, transport: ModelTransport, *, registry: PartsBinToolRegistry,
                 store: ConversationRepository, approvals: ApprovalEngine,
                 max_tool_turns: int = 8, telemetry: AgentTelemetry | None = None) -> None:
        if max_tool_turns < 1:
            raise ValueError("max_tool_turns must be positive")
        self.transport = transport
        self.registry = registry
        self.store = store
        self.approvals = approvals
        if registry.service.repository.storage_id != approvals.repository.storage_id:
            raise ValueError("Approvals and inventory must share one transactional database")
        self.max_tool_turns = max_tool_turns
        self.telemetry = telemetry or AgentTelemetry()

    async def run(self, thread_id: str, user_text: str, *, image: ImageInput | None = None,
                  approval_response: ApprovalResponse | None = None,
                  execution_id: str | None = None,
                  on_event: Callable[[ConversationEvent], None] | None = None) -> RuntimeResult:
        self.store.create_thread(thread_id, self.runtime)
        executions = self.registry.service.repository.executions
        if approval_response is not None:
            record = executions.for_approval(thread_id, approval_response.request_id)
            if record is None:
                raise ValueError("Unknown approval execution for this conversation")
            if execution_id is not None and execution_id != record.execution_id:
                raise ValueError("Approval belongs to another execution")
            execution_id = record.execution_id
        initial = None
        if execution_id is None:
            execution_id = uuid4().hex
            history = tuple(
                {"role": "user" if event.kind == "user_message" else "assistant", "text": str(event.data["text"])}
                for event in self.store.events(thread_id)
                if event.kind in {"user_message", "assistant_text"} and event.data.get("text")
            )
            initial = _TurnState(thread_id, user_text, image, history=history,
                                 had_image=image is not None).checkpoint()
        worker_id = uuid4().hex
        record = executions.claim(thread_id, execution_id, worker_id, initial)
        state = _TurnState.restore(thread_id, record.context, image)
        emitted: list[ConversationEvent] = []
        started = perf_counter()
        domain_outcome: str | None = None

        def emit(key: str, kind: str, data: dict[str, Any]) -> None:
            event = self.store.append_once(ConversationEvent(kind, thread_id, self.runtime,
                {**data, "execution_id": execution_id}), f"{execution_id}:{key}")
            if event in emitted:
                return
            emitted.append(event)
            if on_event is not None and record.status not in {"completed", "failed"}:
                on_event(event)

        def save(status: str = "ready") -> None:
            executions.save(execution_id, worker_id, state.checkpoint(), status)

        def finish(status: str) -> RuntimeResult:
            self.telemetry.turn_finished(thread_id, self.runtime, latency_ms=(perf_counter() - started) * 1000,
                                         status=status, domain_outcome=domain_outcome)
            return RuntimeResult(tuple(emitted), status, execution_id)  # type: ignore[arg-type]

        def terminal(status: str) -> RuntimeResult:
            if state.error:
                emit("error", "error", state.error)
            elif state.pending and state.pending.get("text"):
                emit(f"text:{state.round}", "assistant_text", {"text": state.pending["text"]})
            emit("terminal", "completed", {"status": status})
            finish(status)
            # Replay all saved events so a caller can recover a missed acknowledgement.
            return RuntimeResult(tuple(event for event in self.store.events(thread_id)
                                       if event.data.get("execution_id") == execution_id), status, execution_id)

        try:
            if approval_response is not None:
                # Validate even a duplicate decision on an already completed execution.
                self.approvals.decide(thread_id, approval_response.request_id, approval_response.approved)
            if record.status in {"completed", "failed"}:
                result = terminal(record.status)
                if on_event is not None:
                    for event in result.events:
                        on_event(event)
                return result
            emit("user", "user_message", {"text": state.user_text,
                "image": None if not state.had_image else {"media_type": image.media_type if image else "image/*"}})
            if state.had_image and state.round == 0 and image is None:
                state.error = {"code": "image_resubmission_required", "message": "The interrupted image request needs a fresh photo; image bytes are not stored"}
                save("failed")
                return terminal("failed")
            while True:
                if state.pending is None:
                    if state.round >= self.max_tool_turns:
                        state.error = {"code": "tool_loop_limit", "message": f"Tool loop exceeded {self.max_tool_turns} turns"}
                        save("failed")
                        self.telemetry.loop_limit(thread_id, self.runtime, self.max_tool_turns)
                        return terminal("failed")
                    turn = await self._complete(state)
                    if turn.response_output:
                        state.exchanges.append({"type": "model_output", "items": turn.response_output})
                    state.round += 1
                    state.pending = {"text": turn.text, "calls": [
                        {"name": call.name, "arguments": call.arguments, "call_id": call.call_id}
                        for call in turn.tool_calls]}
                    state.call_index = 0
                    # Persist model output, including provider reasoning, before any effect.
                    save()
                if state.pending["text"]:
                    emit(f"text:{state.round}", "assistant_text", {"text": state.pending["text"]})
                calls = state.pending["calls"]
                if not calls:
                    save("completed")
                    return terminal("completed")
                while state.call_index < len(calls):
                    call = calls[state.call_index]
                    step = f"{state.round}:{state.call_index}"
                    name, args = call["name"], call["arguments"]
                    emit(f"call:{step}", "tool_call", {key: call[key] for key in ("name", "arguments", "call_id")})
                    if state.approval_id:
                        request_id = state.approval_id
                        # A decision persisted before a crash can continue on explicit resume.
                        approval_record = self.approvals.repository.approvals.get(thread_id, request_id)
                        approved = approval_record.decision
                        if approved is None:
                            emit(f"approval:{step}", "approval_request", {
                                "request_id": request_id, "tool": name, "arguments": args,
                                "target": args.get("part_id", args.get("part_ids", "selection")),
                                "effect": _approval_effect(name, args),
                                "specification_review": approval_record.snapshot[str(args["part_id"])].get("specification_review") if name == "apply_specification_review" else None})
                            emit(f"paused:{step}", "completed", {"status": "awaiting_approval"})
                            return finish("awaiting_approval")
                        request = self.approvals.decide(thread_id, request_id, approved)
                        emit(f"decision:{step}", "approval_decision", {"request_id": request_id,
                            "tool": name, "approved": approved})
                        self.telemetry.approval_decided(thread_id, self.runtime, name, approved)
                    if "result" not in call:
                        self.telemetry.tool_started(thread_id, self.runtime, name, args)
                        tool_started = perf_counter()
                        if state.approval_id:
                            result = await self.approvals.execute(request, self.registry,
                                execution_id=execution_id, worker_id=worker_id) if approved else {
                                "ok": False, "error": {"code": "approval_denied", "message": "User declined this operation", "details": {}}}
                        elif call.get("retrieval_started") or (name == "lookup_part_specs" and _lookup_interrupted(state, args.get("part_id"))):
                            result = {"ok": False, "error": {"code": "retrieval_interrupted",
                                "message": "Lookup was interrupted; explicitly request lookup_part_specs to retry", "details": {}}}
                        else:
                            if name == "lookup_part_specs":
                                call["retrieval_started"] = True
                                save()
                            result = await self.registry.execute(name, args, context=ToolExecutionContext(
                                operation_id=f"{execution_id}:{step}", execution_id=execution_id, worker_id=worker_id))
                        self.telemetry.tool_finished(thread_id, self.runtime, name, args,
                            latency_ms=(perf_counter() - tool_started) * 1000, result=result)
                        if result.get("error", {}).get("code") == "approval_required":
                            request = self.approvals.request(thread_id, name, args, service=self.registry.service)
                            state.approval_id = request.request_id
                            save("awaiting_approval")
                            continue
                        call["result"] = result
                        state.exchanges.append({"type": "tool_result", **call, "result": result})
                        # Result is durable before publication. Cursor advances afterwards.
                        save()
                    result = call["result"]
                    emit(f"result:{step}", "tool_result", {"call_id": call["call_id"], "name": name,
                        "arguments": args, "result": result})
                    if result.get("ok"):
                        domain_outcome = _domain_outcome(name, result)
                    state.call_index += 1
                    state.approval_id = None
                    save()
                state.pending = None
                save()
        except Exception:
            self.telemetry.runtime_failure(thread_id, self.runtime, "execution_interrupted")
            raise
        finally:
            executions.release(execution_id, worker_id)

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


def _lookup_interrupted(state: _TurnState, part_id: int | None) -> bool:
    """Require a new user turn before retrying an uncertain paid stage."""
    for exchange in state.exchanges:
        if exchange["type"] != "tool_result":
            continue
        result = exchange["result"]
        if (exchange["name"] == "lookup_part_specs" and exchange["arguments"].get("part_id") == part_id
                and result.get("error", {}).get("code") == "retrieval_interrupted"):
            return True
        added = result.get("result", {})
        if (exchange["name"] == "add_part" and added.get("id") == part_id
                and added.get("enrichment", {}).get("status") == "interrupted"):
            return True
    return False
