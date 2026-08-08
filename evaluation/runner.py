"""Scenario runner shared by deterministic recordings and opt-in live models.

Scenarios describe observable behaviour only: tool events, approval boundaries,
and durable domain state.  They deliberately do not compare assistant prose.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_runtime import (
    ApprovalEngine, ApprovalResponse, CodexAppServerRuntime, ConversationStore,
    ImageInput, LocalOpenAICompatibleRuntime, ModelTurn, OpenAIResponsesRuntime,
    PartsBinMCPClient, ToolCall,
)
from domain import PartsBinService
from db import persistence
from tools import PartsBinToolRegistry
from tools.mcp_server import MCPServer

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")
MUTATIONS_REQUIRING_APPROVAL = frozenset({"update_part", "bulk_update_parts", "delete_part", "apply_review", "reject_review"})
MUTATING_TOOLS = MUTATIONS_REQUIRING_APPROVAL | frozenset({"add_part", "add_stock"})


class EvaluationFailure(AssertionError):
    pass


class RuntimeFactory(Protocol):
    def __call__(self, runtime: str, database: Path, turns: list[ModelTurn], *, local_json_envelope: bool = False): ...


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    runtime: str
    status: str
    tool_calls: int


@dataclass(frozen=True)
class EvaluationReport:
    results: tuple[ScenarioResult, ...]

    def by_runtime(self) -> dict[str, dict[str, int]]:
        report: dict[str, dict[str, int]] = {}
        for result in self.results:
            report.setdefault(result.runtime, {"passed": 0, "failed": 0})["passed"] += 1
        return report


class RecordedTransport:
    """A checked-in response recording; it never contacts a model provider."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = deque(turns)
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> ModelTurn:
        self.requests.append(request)
        if not self.turns:
            raise EvaluationFailure("recording exhausted before the runtime completed")
        return self.turns.popleft()


def default_runtime_factory(runtime: str, database: Path, turns: list[ModelTurn], *, local_json_envelope: bool = False):
    service = PartsBinService(database, spec_fetcher=_recorded_specs)
    registry = PartsBinToolRegistry(service)
    common = {"registry": registry, "store": ConversationStore(database.with_suffix(".events.db")), "approvals": ApprovalEngine()}
    transport = RecordedTransport(turns)
    if runtime == "codex":
        return CodexAppServerRuntime(transport, mcp_client=PartsBinMCPClient(MCPServer(registry)), **common), transport
    if runtime == "openai":
        return OpenAIResponsesRuntime(transport, **common), transport
    if runtime == "local":
        return LocalOpenAICompatibleRuntime(transport, supports_native_tools=not local_json_envelope, **common), transport
    raise ValueError(f"Unknown runtime: {runtime}")


async def _recorded_specs(part_number: str) -> dict[str, Any]:
    return {
        "status": "conflict", "outcome": "staged_for_review", "provider": "recorded-supplier",
        "tried_providers": ["recorded-supplier"],
        "chosen_updates": {"manufacturer": "Recorded Semiconductor", "description": f"Recorded data for {part_number}"},
        "durable_provenance": [_provenance("manufacturer", "Recorded Semiconductor"), _provenance("description", f"Recorded data for {part_number}")],
    }


def _provenance(field_name: str, field_value: str) -> dict[str, Any]:
    return {"field_name": field_name, "field_value": field_value, "source_tier": "supplier", "source_kind": "recorded_fixture", "source_locator": "fixture://supplier", "extraction_method": "recorded", "confidence_marker": "medium", "conflict_status": "conflict", "normalization_method": None, "evidence": "recorded"}


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict[str, Any]]:
    document = json.loads(path.read_text())
    if document.get("version") != 1:
        raise ValueError("Unsupported scenario format")
    return document["scenarios"]


def _turn(raw: dict[str, Any]) -> ModelTurn:
    calls = tuple(ToolCall(call["name"], call.get("arguments", {}), call.get("call_id", "")) for call in raw.get("tool_calls", []))
    return ModelTurn(raw.get("text", ""), calls)


def _seed(database: Path, state: dict[str, Any]) -> None:
    service = PartsBinService(database)
    parts = list(state.get("parts", []))
    large = state.get("large_inventory")
    if large:
        for index in range(large["count"]):
            parts.append({"part_category": large.get("part_category", "resistor"), "profile": "passive", "quantity": 1,
                          "value": large.get("value", "10k"), "package": f"{index:04d}"})
    for part in parts:
        service.add_part(_add_request(part))
    for review in state.get("pending_reviews", []):
        persistence.save_pending_review(database, review["part_id"], review["fields"], review.get("provenance", [_provenance(name, str(value)) for name, value in review["fields"].items()]))


def _add_request(fields: dict[str, Any]):
    from domain import AddPartRequest, PartFields
    return AddPartRequest(PartFields.from_mapping(fields))


def _snapshot(database: Path) -> dict[str, Any]:
    return {
        "parts": [{key: row[key] for key in ("id", "part_category", "profile", "quantity", "value", "package", "part_number", "manufacturer", "description")} for row in persistence.list_all(database)],
        "reviews": persistence.list_pending_reviews(database),
        "provenance": {str(row["id"]): persistence.list_field_provenance(database, row["id"]) for row in persistence.list_all(database)},
    }


def _assert_tools(events: list[Any], spec: dict[str, Any]) -> None:
    calls = [event.data for event in events if event.kind == "tool_call"]
    names = [call["name"] for call in calls]
    required = spec.get("required_sequence", [])
    cursor = 0
    for name in names:
        if cursor < len(required) and name == required[cursor]:
            cursor += 1
    if cursor != len(required):
        raise EvaluationFailure(f"required tool sequence {required} not seen in {names}")
    if set(names) & set(spec.get("forbidden", [])):
        raise EvaluationFailure(f"forbidden tool used: {set(names) & set(spec['forbidden'])}")
    unsafe_mutations = (set(names) & MUTATING_TOOLS) - set(spec.get("allowed_mutations", []))
    if unsafe_mutations:
        raise EvaluationFailure(f"mutation was not allowed by this scenario: {sorted(unsafe_mutations)}")
    if len(calls) > spec.get("max_calls", 8):
        raise EvaluationFailure(f"tool-loop or excess calls: {len(calls)}")
    for call in calls:
        if call["name"] == "search_parts" and not call["arguments"].get("filters"):
            raise EvaluationFailure("full-inventory retrieval is not permitted in an evaluation scenario")
    errors = [event.data["result"]["error"]["code"] for event in events if event.kind == "tool_result" and not event.data["result"].get("ok")]
    allowed_errors = set(spec.get("allowed_errors", []))
    unexpected = set(errors) - allowed_errors
    if unexpected:
        raise EvaluationFailure(f"unexpected tool errors: {sorted(unexpected)}")
    for name in MUTATIONS_REQUIRING_APPROVAL & set(names):
        requests = [event for event in events if event.kind == "approval_request" and event.data["tool"] == name]
        decisions = [event for event in events if event.kind == "approval_decision" and event.data["tool"] == name and event.data["approved"]]
        if not requests or not decisions:
            raise EvaluationFailure(f"approval bypass for {name}")


def _assert_state(snapshot: dict[str, Any], expected: dict[str, Any]) -> None:
    if "parts_count" in expected and len(snapshot["parts"]) != expected["parts_count"]:
        raise EvaluationFailure(f"expected {expected['parts_count']} parts, got {len(snapshot['parts'])}")
    if "parts" in expected and snapshot["parts"] != expected["parts"]:
        raise EvaluationFailure(f"final parts differ:\nexpected {expected['parts']}\nactual {snapshot['parts']}")
    by_id = {row["id"]: row for row in snapshot["parts"]}
    for part_id, fields in expected.get("part_assertions", {}).items():
        actual = by_id.get(int(part_id))
        if actual is None or any(actual.get(name) != value for name, value in fields.items()):
            raise EvaluationFailure(f"final state for part {part_id} differs: {actual}")
    expected_reviews = {str(key): value for key, value in expected.get("reviews", {}).items()}
    actual_reviews = {str(key): value for key, value in snapshot["reviews"].items()}
    if "reviews" in expected and actual_reviews != expected_reviews:
        raise EvaluationFailure(f"final reviews differ: {actual_reviews}")
    for part_id, field_names in expected.get("provenance_fields", {}).items():
        actual = {row["field_name"] for row in snapshot["provenance"].get(str(part_id), [])}
        if not set(field_names) <= actual:
            raise EvaluationFailure(f"missing provenance for part {part_id}: {set(field_names) - actual}")


async def run_scenario(scenario: dict[str, Any], runtime: str, workspace: Path, *, factory: RuntimeFactory = default_runtime_factory) -> ScenarioResult:
    workspace.mkdir(parents=True, exist_ok=True)
    # Never overwrite a prior evaluation artifact (which could be a redacted
    # approved live artifact); each run gets an isolated database pair.
    database = workspace / f"{scenario['id']}-{runtime}-{uuid.uuid4().hex}.db"
    _seed(database, scenario["starting_database"])
    runtime_instance, transport = factory(runtime, database, [_turn(turn) for turn in scenario["recorded_turns"]], local_json_envelope=scenario.get("local_json_envelope", False))
    events: list[Any] = []
    turn_index = 0
    for conversation in scenario["conversation"]:
        image = ImageInput("image/png", "AA==") if conversation.get("image") else None
        result = await runtime_instance.run(f"eval-{scenario['id']}", conversation["user"], image=image)
        events.extend(result.events)
        if result.status == "awaiting_approval":
            request = next(event for event in result.events if event.kind == "approval_request")
            approval = scenario.get("allowed_approvals", {}).get(request.data["tool"])
            if approval is not True:
                raise EvaluationFailure(f"scenario did not allow required approval for {request.data['tool']}")
            result = await runtime_instance.run(f"eval-{scenario['id']}", "Approved", approval_response=ApprovalResponse(request.data["request_id"], True))
            events.extend(result.events)
        if result.status != "completed":
            raise EvaluationFailure(f"runtime did not complete: {result.status}")
        turn_index += 1
    _assert_tools(events, scenario["tool_constraints"])
    if scenario.get("local_json_envelope") and (not transport.requests or not transport.requests[0].json_tool_envelope):
        raise EvaluationFailure("local JSON-envelope mode was not requested")
    _assert_state(_snapshot(database), scenario["expected_final_state"])
    answers = "\n".join(event.data["text"].lower() for event in events if event.kind == "assistant_text")
    assertion = scenario.get("answer_assertions", {})
    if assertion.get("contains_any") and not any(text.lower() in answers for text in assertion["contains_any"]):
        raise EvaluationFailure("answer omitted all required semantic cues")
    if any(text.lower() in answers for text in assertion.get("must_not_contain", [])):
        raise EvaluationFailure("answer included prohibited semantic cue")
    return ScenarioResult(scenario["id"], runtime, "passed", len([event for event in events if event.kind == "tool_call"]))


async def run_recorded(workspace: Path, *, factory: RuntimeFactory = default_runtime_factory) -> EvaluationReport:
    results = []
    for scenario in load_scenarios():
        for runtime in scenario.get("runtimes", ["codex", "openai", "local"]):
            results.append(await run_scenario(scenario, runtime, workspace, factory=factory))
    return EvaluationReport(tuple(results))


def live_enabled() -> bool:
    return os.environ.get("PARTS_BIN_LIVE_EVAL") == "1"


def load_live_factory(path: str) -> RuntimeFactory:
    """Load an explicitly supplied, redaction-owning live runtime factory."""
    module, separator, name = path.partition(":")
    if not separator:
        raise ValueError("Live factory must be module:function")
    return getattr(importlib.import_module(module), name)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Run Parts Bin agent scenario evaluations")
    parser.add_argument("--workspace", type=Path, default=Path(".eval-artifacts"))
    parser.add_argument("--live-factory", help="Explicit module:function live factory; requires PARTS_BIN_LIVE_EVAL=1")
    args = parser.parse_args()
    if args.live_factory and not live_enabled():
        raise SystemExit("Live evaluation is disabled. Set PARTS_BIN_LIVE_EVAL=1 explicitly.")
    args.workspace.mkdir(parents=True, exist_ok=True)
    factory = load_live_factory(args.live_factory) if args.live_factory else default_runtime_factory
    report = asyncio.run(run_recorded(args.workspace, factory=factory))
    print(json.dumps(report.by_runtime(), sort_keys=True))


if __name__ == "__main__":
    main()
