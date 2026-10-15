"""Bounded OpenAI agent loop with server-owned tools and approvals."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4

from tools import PartsBinToolRegistry, ToolExecutionContext

from .approval import ApprovalEngine
from .budgets import ModelBudgetError, bounded_history
from .models import ApprovalResponse, ConversationEvent, ImageInput, ModelTurn, RuntimeResult
from .store import ConversationRepository
from .telemetry import AgentTelemetry, _domain_outcome

SYSTEM_INSTRUCTIONS = """You are the Parts Bin assistant. Inventory facts must be discovered with Parts Bin tools; never assume or list unseen inventory. Use tools for every inventory fact and mutation.
Use electronics knowledge to identify named components, distinguishing it from stored inventory facts. When the user requests adding stock, add identifiable ICs with a functional category and meaningful description, rather than a generic IC label. Do not invent manufacturer, package, or electrical specifications from an ambiguous base part number. Preserve user-provided quantity and package. Unnumbered connectors, modules, kits and other non-passive stock remain distinct physical groups even when descriptions or packages agree. Ask for the existing part ID before increasing such stock; do not invent ordering codes to combine it.
For identified transistors, use part_category bjt for bipolar junction transistors and mosfet for MOSFETs. A generic transistor label does not establish either subtype; ask for markings or an exact part number before choosing. Existing transistor records keep their identity unless the user explicitly requests a supported category correction; do not silently relabel or merge them. Generic transistor records have no electrical specification contract.
Identification requests (including 'identify this part' and photo identification) request an explanation, not an inventory mutation. Do not add, update, stage, merge, or delete stock unless the user explicitly requests that operation. Do not ask for stock quantity during identification. If exact identity remains unresolved, end with a targeted question for markings, manufacturer, a product link, or a clearer package photo; a generic definition alone is insufficient. For ambiguous labels such as ARGB LED, ask whether it is a component, strip, or lighting assembly and request a link or markings. Do not infer its voltage, protocol, controller, or pinout. Preserve user-supplied packages as assertions; ask for manufacturer or markings before claiming an exact variant. Inventory lookup is not necessary merely to explain a named component.
Before choosing a part_category filter for inventory lookup, call list_categories to discover the exact stored names. Reuse discovery within the current request. Select relevant returned categories instead of guessing labels; search each relevant category when multiple names apply. Category totals include zero-stock records and do not establish electrical suitability.
For category lookup, op amp, opamp, op-amp, and operational amplifier are search synonyms. Do not infer profile or package filters from a functional category. An empty filtered search only establishes no matches for those filters; relax inferred filters before claiming the inventory contains none.
For inventory lookup, translate nominal values and required stock into search_parts filters and minimum_quantity. Preserve exact ordering-code suffixes. For a simple lookup without an explicit requested count, omit minimum_quantity and do not repeat an empty value/package search by changing only the stock threshold. If a search for an explicit requested stock count is empty, search the same nominal filters without minimum_quantity, report available quantities per record, and clarify acceptable packages rather than treating distinct variants as interchangeable. An empty stock-constrained result does not mean no parts exist. When no single record has the requested quantity, say that explicitly and ask which package is acceptable; do not answer yes by summing different packages or ordering variants. If package or units are ambiguous, ask a targeted question; do not infer electrical suitability from nominal value, description, or pending enrichment. When an electrical category is explicitly named and has a supported contract, use that contract directly without list_categories. Use default search page sizes unless a concrete need requires more. For electrical requirements, call get_specification_contract for the category once when its contract is not already available in this conversation, then query supported requirements with explicit units, basis, and conditions. Combine all known constraints in one search_parts call; inspect individual get_specifications only for returned incomplete candidates or unresolved qualifiers. Do not repeat unchanged searches or rediscover unchanged categories within the same request. Use linear_regulator (LDO), buck_converter, or led contracts for regulator and LED searches. Do not infer regulator topology or LED identity from appearance. For input supply suitability compare both operating endpoints, never the absolute maximum. Fixed output voltage, adjustable output range, dropout and switch current limit are distinct. LED voltage, wavelength and intensity require their test current and temperature; unmarked LEDs need clarification. Unknown fields and ambiguous categories need clarification. Preserve requested condition qualifiers in their names (for example, ambient temperature uses ambient_temperature, not temperature). If the qualifier is unclear, inspect get_specifications for the candidate records and ask which conditions apply. When explicitly asked about pending electrical reviews, discover them with list_pending_specification_reviews, then inspect get_specifications for a returned part_id before making claims about proposed ratings. list_pending_reviews covers base metadata only. Compare source-backed matches separately from incomplete candidates; absolute maxima and thresholds do not establish application suitability. User assertions may be staged with stage_specification_review and accepted through apply_specification_review, but remain assertions. For a supplied manufacturer PDF and an exact committed ordering code, use ingest_datasheet to retrieve and stage source-backed electrical facts. Inspect get_specifications and submit apply_specification_review for server approval. Never invent source facts or accept proposals automatically. Unmarked or ambiguous stock needs identity clarification first; lookup_part_specs retrieves base metadata only.
Inventory availability is based on committed records and accepted specifications. Do not call list_pending_reviews or list_pending_specification_reviews during an ordinary inventory lookup or BOM availability check, including when a row is missing. Query pending reviews only when the user explicitly asks about pending changes, review status, or approval work. In those requests, use narrow value, full part_number, or part_id filters and follow next_offset with unchanged arguments until null. Inspect committed identity and stock with get_part when discussing a proposed change. A pending review fields map contains field objects: read proposed text from fields[field_name]["value"]. The accepted flag selects a proposal for review; it does not mean the proposal is committed. Keep proposed fields distinct from committed stock, and never apply or reject reviews during a read-only lookup.
For a pasted BOM, CSV/TSV table, or list of part numbers, use check_inventory. A bare pasted BOM or list requests an availability check unless the user explicitly asks for another action. Extract every row and preserve full ordering codes including commas and suffixes. Distinguish reference designators, supplier SKUs, manufacturer part numbers, and quantities. Do not invent missing quantities, packages, or units. Submit all parsed rows in one check_inventory call, up to 100 rows; deduplication is optional and the server handles identical rows and explicit quantities. Use returned input_indices to account for every original row. Missing quantities remain unknown, and overlapping_requirements requires clarification before claiming total BOM coverage. For longer lists, submit bounded groups of at most 100 rows. Discover categories for generic value-based rows. Check identifiable rows and ask targeted questions for ambiguous rows. If check_inventory rejects missing identity and returns input_indices, those zero-based indices identify every unresolved submitted row. Ask for the missing ordering code or category and value; never invent them to make the call succeed or label rejected rows as missing stock. Account for every input row in a compact results table showing requested item, required quantity when supplied, available quantity and package per record, and found/missing/out-of-stock/insufficient status. Sufficiency uses one matching record; never sum distinct variants or reuse stock for overlapping generic and exact requirements. Disclose unresolved overlap and ask which stock may be used. These checks do not reserve stock. Verify electrical requirements separately with specification tools before claiming coverage. Offer partial candidate matches separately, never as exact stock. Do not add, enrich, modify inventory, or inspect pending reviews merely because a BOM was pasted.
For descriptions, manufacturer names, partial part numbers, or recorded markings, use search_candidates with literal search terms. Combine known manufacturer, part fragment, category, value or package terms when narrowing candidates; each whitespace-separated term must occur on the same committed record, possibly in different fields. Omit conversational filler and do not infer package or electrical facts. Punctuation is literal; nominal-unit equivalence needs structured search_parts filters. These results identify candidates only. Preserve exact returned ordering codes and suffixes, report stock per record, and clarify ambiguous identity before adding or merging stock. A marking absent from committed descriptions and part numbers cannot be discovered by this tool. Use search_parts for exact ordering-code lookup.
Search pages: use the returned next_offset with unchanged search arguments and limit to retrieve remaining results when completeness is needed; stop when next_offset is null. Specification matches and incomplete candidates paginate separately using the same offset. Never claim a truncated page is the entire inventory. If stock or accepted facts change during pagination, restart the search.
Nominal quantities are stored by the domain as exact decimal base units with value_unit. Use display_value for readable labels. Resistance uses ohms, capacitance farads, inductance henries, current amperes and voltage volts. Submit explicit units or value_unit when the category does not establish a dimension. Preserve manufacturer ordering codes and evidence text. Never interpret a nominal value as an electrical limit or confirmed suitability.
Search before adding stock. A matching base part number does not establish that different package variants are the same stock. Clarify before merging uncertain variants. 'I have 10, not 100' sets quantity to 10; it is not an increment.
For explicit multi-part additions, check all supplied identities in one search_parts expression or check_inventory call, then submit all missing distinct records in one add_parts call (up to 100 items). Use nested all/any expressions to combine shared category/package filters with alternate values or ordering codes. Do not search or add each row in separate model turns, repeat category discovery already available in this request, or fetch individual records when the check already returned the needed fields. Add and dedupe means avoid duplicating stock already counted: skip existing matching records and clarify whether additional physical stock should increment them. Use add_stock_batch only for explicitly additional stock at known part_ids. Use update_parts for different field changes across multiple records with one approval. Report all outcomes, and never retry a committed mutation merely because a response was interrupted. Bulk add_parts intentionally performs no automatic enrichment; preserve supplied facts and request lookups only when the user asks.
Generic passive stock such as resistors, capacitors, and inductors does not need manufacturer lookup. When adding assortment packs or kits, set enrich=false on every add_part call for their contents, including identified ICs, unless the user explicitly requests enrichment. Also set enrich=false when the user asks to skip lookups or extraction. Preserve supplied values, identities, quantities, and descriptions; do not invent missing kit contents or per-type quantities. A skipped enrichment is intentional: do not follow it with lookup_part_specs or ingest_datasheet unless the user explicitly asks.
Adding an identified IC outside these cases stages supplier details automatically; inspect the enrichment outcome. Use lookup_part_specs to retry unavailable lookups or enrich existing parts when requested. Explain lookup failures or pending reviews; staged proposals are not committed facts. Submit update_part or apply_review for corrections and accepted enrichment so the server presents approval controls. Do not ask for approval only in prose or claim tools cannot be approved in this session.
Photos are supplied only on the first model call. Before calling tools for a photo, write a concise description of the visible markings, identity, package, quantity, and uncertainties in your response so subsequent calls can use it. Do not guess unreadable details or infer the original package, fabrication process, or circuit function from an exposed die or internal layout. If later work needs another look, ask for a new photo.
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
    async def start(self, request: ModelRequest) -> str: ...
    async def retrieve(self, response_id: str) -> ModelTurn | None: ...
    async def cancel(self, response_id: str) -> str: ...


def _input_digest(user_text: str, image: ImageInput | None) -> str:
    image_digest = None if image is None else {
        "media_type": image.media_type,
        "digest": hashlib.sha256(image.data_base64.encode()).hexdigest()}
    return hashlib.sha256(json.dumps({"text": user_text, "image": image_digest},
                                    sort_keys=True).encode()).hexdigest()


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
    input_digest: str | None = None
    model_submission_started: bool = False
    model_response_id: str | None = None
    cancellation_outcome: str | None = None

    def checkpoint(self) -> dict[str, Any]:
        # Image bytes never enter durable execution state.
        return {"user_text": self.user_text, "exchanges": self.exchanges, "history": self.history,
                "round": self.round, "pending": self.pending, "call_index": self.call_index,
                "approval_id": self.approval_id, "had_image": self.had_image, "error": self.error,
                "input_digest": self.input_digest, "model_submission_started": self.model_submission_started,
                "model_response_id": self.model_response_id, "cancellation_outcome": self.cancellation_outcome}

    @classmethod
    def restore(cls, thread_id: str, context: dict[str, Any], image: ImageInput | None):
        return cls(thread_id, context["user_text"], image, list(context["exchanges"]),
                   tuple(context["history"]), context["round"], context["pending"],
                   context["call_index"], context["approval_id"], context["had_image"], context["error"],
                   context.get("input_digest"), context.get("model_submission_started", False),
                   context.get("model_response_id"), context.get("cancellation_outcome"))


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

    def prepare(self, thread_id: str, execution_id: str, user_text: str, *,
                image: ImageInput | None = None, durable: bool = False,
                reservation: str | None = None) -> str:
        """Accept a stable request identity before any paid work or dispatch."""
        self.store.create_thread(thread_id, self.runtime)
        initial = self._initial_state(thread_id, user_text, image)
        initial["input_digest"] = _input_digest(user_text, image)
        repository = self.registry.service.repository
        if durable:
            repository.dispatch.accept(thread_id, execution_id, initial, reservation)
            self.store.append_once(ConversationEvent('user_message', thread_id, self.runtime,
                {'text': user_text, 'image': {'media_type': image.media_type} if image else None,
                 'execution_id': execution_id}), f'{execution_id}:user')
        else:
            repository.executions.prepare(thread_id, execution_id, initial)
        return execution_id

    def _initial_state(self, thread_id: str, user_text: str, image: ImageInput | None) -> dict[str, Any]:
        history = bounded_history(tuple(
            {"role": "user" if event.kind == "user_message" else "assistant", "text": str(event.data["text"])}
            for event in self.store.events(thread_id)
            if event.kind in {"user_message", "assistant_text"} and event.data.get("text")
        ))
        return _TurnState(thread_id, user_text, image, history=history,
                          had_image=image is not None).checkpoint()

    async def run(self, thread_id: str, user_text: str, *, image: ImageInput | None = None,
                  approval_response: ApprovalResponse | None = None,
                  execution_id: str | None = None,
                  on_event: Callable[[ConversationEvent], None] | None = None) -> RuntimeResult:
        return await self._run(thread_id, user_text, image=image,
                               approval_response=approval_response, execution_id=execution_id,
                               on_event=on_event, single_step=False, background=False)

    async def advance(self, thread_id: str, user_text: str = "", *, image: ImageInput | None = None,
                      approval_response: ApprovalResponse | None = None,
                      execution_id: str | None = None) -> RuntimeResult:
        """Checkpoint one model response or tool call, then release worker ownership.

        Continue a ready result with its execution ID. This bounds the number of
        stages, not the duration of a provider call. It does not schedule work.
        """
        return await self._run(thread_id, user_text, image=image,
                               approval_response=approval_response, execution_id=execution_id,
                               on_event=None, single_step=True, background=False)

    async def advance_background(self, thread_id: str, execution_id: str, *,
                                 image: ImageInput | None = None,
                                 approval_response: ApprovalResponse | None = None) -> RuntimeResult:
        """Submit or collect one model request, or advance one tool stage.

        Intake must already be prepared. Waiting results release the lease and
        require a later invocation; this method never polls in a loop.
        """
        if not execution_id:
            raise ValueError("A prepared execution ID is required")
        return await self._run(thread_id, "", image=image,
                               approval_response=approval_response, execution_id=execution_id,
                               on_event=None, single_step=True, background=True)

    async def _run(self, thread_id: str, user_text: str, *, image: ImageInput | None,
                   approval_response: ApprovalResponse | None,
                   execution_id: str | None,
                   on_event: Callable[[ConversationEvent], None] | None,
                   single_step: bool, background: bool) -> RuntimeResult:
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
            initial = self._initial_state(thread_id, user_text, image)
        worker_id = uuid4().hex
        record = executions.claim(thread_id, execution_id, worker_id, initial)
        state = _TurnState.restore(thread_id, record.context, image)
        emitted: list[ConversationEvent] = []
        started = perf_counter()
        domain_outcome: str | None = None
        advanced = False

        def emit(key: str, kind: str, data: dict[str, Any]) -> None:
            event = self.store.append_once(ConversationEvent(kind, thread_id, self.runtime,
                {**data, "execution_id": execution_id}), f"{execution_id}:{key}")
            if event in emitted:
                return
            emitted.append(event)
            if on_event is not None and record.status not in {"completed", "failed", "cancelled"}:
                on_event(event)

        def save(status: str = "ready") -> None:
            executions.save(execution_id, worker_id, state.checkpoint(), status)

        def finish(status: str) -> RuntimeResult:
            self.telemetry.turn_finished(thread_id, self.runtime, latency_ms=(perf_counter() - started) * 1000,
                                         status=status, domain_outcome=domain_outcome)
            return RuntimeResult(tuple(emitted), status, execution_id)  # type: ignore[arg-type]

        def terminal(status: str) -> RuntimeResult:
            if status == "cancelled":
                pass
            elif state.error:
                emit("error", "error", state.error)
            elif state.pending and state.pending.get("text"):
                emit(f"text:{state.round}", "assistant_text", {"text": state.pending["text"]})
            emit("terminal", "completed", {"status": status, **({"provider_outcome": state.cancellation_outcome} if status == "cancelled" else {})})
            finish(status)
            # Replay all saved events so a caller can recover a missed acknowledgement.
            return RuntimeResult(tuple(event for event in self.store.events(thread_id)
                                       if event.data.get("execution_id") == execution_id), status, execution_id)

        async def cancel() -> RuntimeResult:
            # Recover committed receipts/publication without executing any tool.
            if state.pending:
                for index, call in enumerate(state.pending["calls"]):
                    step = f"{state.round}:{index}"
                    receipt = self.registry.service.repository.operations.get(f"{execution_id}:{step}")
                    result = call.get("result") or (receipt.result if receipt else None)
                    if result is None and state.approval_id and index == state.call_index:
                        approval = self.approvals.repository.approvals.get(thread_id, state.approval_id)
                        result = approval.result if approval else None
                    if result is not None:
                        emit(f"result:{step}", "tool_result", {"call_id": call["call_id"],
                            "name": call["name"], "arguments": call["arguments"], "result": result})
            if state.model_response_id:
                state.cancellation_outcome = await self.transport.cancel(state.model_response_id)
            else:
                state.cancellation_outcome = "submission_uncertain" if state.model_submission_started else "no_inflight_response"
            save("cancelled")
            return terminal("cancelled")

        try:
            if approval_response is not None and executions.cancellation_requested(execution_id):
                raise ValueError("This execution was cancelled; start a new request")
            if approval_response is not None:
                # Validate even a duplicate decision on an already completed execution.
                self.approvals.decide(thread_id, approval_response.request_id, approval_response.approved)
            if record.status in {"completed", "failed", "cancelled"}:
                result = terminal(record.status)
                if on_event is not None:
                    for event in result.events:
                        on_event(event)
                return result
            if not executions.begin_stage(execution_id, worker_id):
                return await cancel()
            emit("user", "user_message", {"text": state.user_text,
                "image": None if not state.had_image else {"media_type": image.media_type if image else "image/*"}})
            if (state.round == 0 and not state.model_submission_started and state.input_digest
                    and image is not None and _input_digest(state.user_text, image) != state.input_digest):
                raise ModelBudgetError("image_identity_mismatch",
                    "The supplied photo differs from this accepted request. Start a new request for a different photo.")
            if state.had_image and state.round == 0 and image is None and not state.model_submission_started:
                state.error = {"code": "image_resubmission_required", "message": "The interrupted image request needs a fresh photo; image bytes are not stored"}
                save("failed")
                return terminal("failed")
            while True:
                if not executions.begin_stage(execution_id, worker_id):
                    return await cancel()
                if state.pending is None:
                    if single_step and advanced:
                        return finish("ready")
                    if state.round >= self.max_tool_turns:
                        state.error = {"code": "tool_loop_limit", "message": f"Tool loop exceeded {self.max_tool_turns} turns"}
                        save("failed")
                        self.telemetry.loop_limit(thread_id, self.runtime, self.max_tool_turns)
                        return terminal("failed")
                    if state.model_submission_started:
                        if state.model_response_id is None:
                            raise ModelBudgetError("model_submission_uncertain",
                                "Model submission was interrupted before its response ID was saved. Inspect provider usage before starting a new request; this execution will not resubmit it.")
                        turn = await self.transport.retrieve(state.model_response_id)
                        if turn is None:
                            save("waiting_model")
                            return finish("waiting_model")
                    elif background:
                        # Fence uncertain submission before contacting the provider.
                        state.model_submission_started = True
                        save()
                        state.model_response_id = await self.transport.start(self._model_request(state))
                        save("waiting_model")
                        return finish("waiting_model")
                    else:
                        turn = await self._complete(state)
                    if turn.response_output:
                        state.exchanges.append({"type": "model_output", "items": turn.response_output})
                    state.round += 1
                    state.pending = {"text": turn.text, "calls": [
                        {"name": call.name, "arguments": call.arguments, "call_id": call.call_id}
                        for call in turn.tool_calls]}
                    state.call_index = 0
                    state.model_response_id = None
                    state.model_submission_started = False
                    # Persist model output, including provider reasoning, before any effect.
                    save()
                    advanced = True
                if not executions.begin_stage(execution_id, worker_id):
                    return await cancel()
                if state.pending["text"]:
                    emit(f"text:{state.round}", "assistant_text", {"text": state.pending["text"]})
                calls = state.pending["calls"]
                if not calls:
                    save("completed")
                    return terminal("completed")
                while state.call_index < len(calls):
                    if not executions.begin_stage(execution_id, worker_id):
                        return await cancel()
                    if single_step and advanced:
                        return finish("ready")
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
                        elif call.get("retrieval_started") or (name in {"lookup_part_specs", "ingest_datasheet"} and _lookup_interrupted(state, args.get("part_id"), name)):
                            result = {"ok": False, "error": {"code": "retrieval_interrupted",
                                "message": f"Lookup was interrupted; explicitly request {name} in a new turn to retry", "details": {}}}
                        else:
                            if name in {"lookup_part_specs", "ingest_datasheet"}:
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
                    advanced = True
                state.pending = None
                save()
        except ModelBudgetError as exc:
            if executions.cancellation_requested(execution_id):
                return await cancel()
            state.error = {"code": exc.code, "message": str(exc)}
            save("failed")
            self.telemetry.runtime_failure(thread_id, self.runtime, exc.code)
            return terminal("failed")
        except asyncio.CancelledError:
            self.telemetry.runtime_failure(thread_id, self.runtime, "execution_interrupted")
            self.telemetry.turn_finished(thread_id, self.runtime,
                latency_ms=(perf_counter() - started) * 1000, status="interrupted",
                domain_outcome=domain_outcome)
            raise
        except Exception:
            self.telemetry.runtime_failure(thread_id, self.runtime, "execution_interrupted")
            raise
        finally:
            executions.release(execution_id, worker_id)

    async def _complete(self, state: _TurnState) -> ModelTurn:
        return await self.transport.complete(self._model_request(state))

    def _model_request(self, state: _TurnState) -> ModelRequest:
        tools = tuple({"type": "function", "name": tool["name"],
                       "description": tool["description"], "parameters": tool["inputSchema"],
                       # Patch-style tools intentionally distinguish omitted fields from null.
                       # The registry validates inputs; strict mode would require every field.
                       "strict": False} for tool in self.registry.list_tools())
        return ModelRequest(
            SYSTEM_INSTRUCTIONS, state.user_text, state.image if state.round == 0 else None, tools,
            tuple(state.exchanges), thread_id=state.thread_id, history=bounded_history(state.history),
        )


def _approval_effect(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "update_parts":
        return "Update parts: " + "; ".join(
            f"#{item['part_id']}: " + ", ".join(f"{key}={value!r}" for key, value in item['fields'].items())
            for item in arguments["items"])
    fields = arguments.get("fields") or arguments.get("updates")
    if isinstance(fields, dict):
        return f"{tool_name}: change {', '.join(sorted(fields))}"
    if isinstance(fields, list):
        return f"{tool_name}: reject {', '.join(fields)}"
    return tool_name.replace("_", " ")


def _lookup_interrupted(state: _TurnState, part_id: int | None, tool_name: str = 'lookup_part_specs') -> bool:
    """Require a new user turn before retrying an uncertain paid stage."""
    for exchange in state.exchanges:
        if exchange["type"] != "tool_result":
            continue
        result = exchange["result"]
        if (exchange["name"] == tool_name and exchange["arguments"].get("part_id") == part_id
                and result.get("error", {}).get("code") == "retrieval_interrupted"):
            return True
        added = result.get("result", {})
        if (tool_name == 'lookup_part_specs' and exchange["name"] == "add_part" and added.get("id") == part_id
                and added.get("enrichment", {}).get("status") == "interrupted"):
            return True
    return False
