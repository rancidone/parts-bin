"""Redacted live-evaluation failure capture and review-gated promotion.

Capture artifacts are deliberately not replay recordings.  An operator creates
a separate synthetic scenario after review; promotion records who approved it
and appends that reviewed scenario to the Phase 05 suite.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.models import ConversationEvent

from .runner import SCENARIOS_PATH

CAPTURE_VERSION = 1


def redacted_event_summary(events: list[ConversationEvent]) -> list[dict[str, Any]]:
    """Keep event categories/codes only; never retain text, arguments, images, or results."""
    summary: list[dict[str, Any]] = []
    for event in events:
        item: dict[str, Any] = {"kind": event.kind}
        if event.kind in {"tool_call", "tool_result", "approval_request", "approval_decision"}:
            item["tool"] = str(event.data.get("name") or event.data.get("tool") or "unknown")
        if event.kind == "tool_result":
            error = event.data.get("result", {}).get("error", {})
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                item["error_code"] = error["code"]
        if event.kind == "error" and isinstance(event.data.get("code"), str):
            item["error_code"] = event.data["code"]
        if event.kind == "approval_decision":
            item["approved"] = bool(event.data.get("approved"))
        if event.kind == "completed":
            item["status"] = str(event.data.get("status", "unknown"))
        # user_message, assistant_text, and all payload values are omitted.
        summary.append(item)
    return summary


def capture_failure(path: Path, *, runtime: str, failure_code: str, events: list[ConversationEvent],
                    scenario_id: str | None = None) -> dict[str, Any]:
    """Write a safe diagnostic artifact suitable for operator review."""
    document = {
        "version": CAPTURE_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime,
        "scenario_id": scenario_id,
        "failure_code": failure_code,
        "events": redacted_event_summary(events),
        "promotion": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def promote_failure(capture_path: Path, candidate_path: Path, *, approved_by: str,
                    scenarios_path: Path = SCENARIOS_PATH) -> str:
    """Append a reviewed synthetic scenario after explicit named approval."""
    if not approved_by.strip():
        raise ValueError("Promotion requires a non-empty approved_by reviewer")
    capture = json.loads(capture_path.read_text())
    if capture.get("version") != CAPTURE_VERSION:
        raise ValueError("Unsupported failure capture format")
    candidate = json.loads(candidate_path.read_text())
    required = {"id", "starting_database", "conversation", "tool_constraints", "expected_final_state", "recorded_turns"}
    missing = required - set(candidate) if isinstance(candidate, dict) else required
    if not isinstance(candidate, dict) or missing:
        raise ValueError(f"Candidate scenario is missing required fields: {sorted(missing) if isinstance(candidate, dict) else sorted(required)}")
    # The candidate is intentionally authored after review. It must be a new
    # deterministic fixture, not an unredacted capture copied into git.
    document = json.loads(scenarios_path.read_text())
    if any(item.get("id") == candidate["id"] for item in document["scenarios"]):
        raise ValueError(f"Scenario already exists: {candidate['id']}")
    document["scenarios"].append(candidate)
    scenarios_path.write_text(json.dumps(document, indent=2) + "\n")
    capture["promotion"] = {"approved_by": approved_by, "approved_at": datetime.now(timezone.utc).isoformat(),
                            "scenario_id": candidate["id"]}
    capture_path.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")
    return candidate["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture or promote redacted Parts Bin evaluation failures")
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Create a metadata-only failure artifact")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--runtime", choices=("codex", "openai", "local"), required=True)
    capture.add_argument("--failure-code", required=True)
    capture.add_argument("--scenario-id")
    # Events cannot be reconstructed safely from arbitrary JSON at this CLI
    # boundary. Integrations call capture_failure directly with normalized events.
    promote = commands.add_parser("promote", help="Append an explicitly reviewed synthetic scenario")
    promote.add_argument("--capture", type=Path, required=True)
    promote.add_argument("--candidate", type=Path, required=True)
    promote.add_argument("--approved-by", required=True)
    promote.add_argument("--scenarios", type=Path, default=SCENARIOS_PATH)
    args = parser.parse_args()
    if args.command == "capture":
        capture_failure(args.output, runtime=args.runtime, failure_code=args.failure_code, scenario_id=args.scenario_id, events=[])
        return
    print(promote_failure(args.capture, args.candidate, approved_by=args.approved_by, scenarios_path=args.scenarios))


if __name__ == "__main__":
    main()
