"""Privacy-preserving telemetry for normalized agent execution.

Records intentionally contain identifiers only as one-way, per-process
fingerprints.  They never contain user text, image bytes, tool values, tool
results, exception messages, or inventory records.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import log

TelemetryEmitter = Callable[..., None]

_DOMAIN_ERROR_CODES = frozenset({
    "part_not_found", "review_not_found", "duplicate_part", "conflict",
    "ambiguous_target", "incomplete_input",
})


def fingerprint(value: Any) -> str:
    """Return a non-reversible correlation value; never retain source content."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


class AgentTelemetry:
    """Small fixed-schema emitter shared by all runtimes and the gateway."""

    def __init__(self, emit: TelemetryEmitter = log.emit_telemetry) -> None:
        self._emit = emit

    def runtime_selected(self, thread_id: str, runtime: str) -> None:
        self._emit("agent_runtime_selected", thread_hash=fingerprint(thread_id), runtime=runtime)

    def turn_finished(self, thread_id: str, runtime: str, *, latency_ms: float, status: str,
                      domain_outcome: str | None = None) -> None:
        self._emit("agent_turn_finished", thread_hash=fingerprint(thread_id), runtime=runtime,
                   latency_ms=round(latency_ms, 1), status=status,
                   domain_outcome=domain_outcome or "none")

    def tool_started(self, thread_id: str, runtime: str, name: str, arguments: dict[str, Any]) -> None:
        self._emit("agent_tool_started", thread_hash=fingerprint(thread_id), runtime=runtime,
                   tool=name, argument_keys=sorted(arguments), argument_fingerprint=fingerprint(arguments))

    def tool_finished(self, thread_id: str, runtime: str, name: str, arguments: dict[str, Any], *,
                      latency_ms: float, result: dict[str, Any]) -> None:
        error = result.get("error") if isinstance(result, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code", "unknown"))
            self._emit("agent_tool_error", thread_hash=fingerprint(thread_id), runtime=runtime, tool=name,
                       argument_keys=sorted(arguments), argument_fingerprint=fingerprint(arguments),
                       latency_ms=round(latency_ms, 1), error_code=code,
                       failure_scope="domain" if code in _DOMAIN_ERROR_CODES else "tool")
            return
        outcome = _domain_outcome(name, result)
        self._emit("agent_tool_finished", thread_hash=fingerprint(thread_id), runtime=runtime, tool=name,
                   argument_keys=sorted(arguments), argument_fingerprint=fingerprint(arguments),
                   latency_ms=round(latency_ms, 1), domain_outcome=outcome)

    def approval_decided(self, thread_id: str, runtime: str, tool: str, approved: bool) -> None:
        self._emit("agent_approval_decision", thread_hash=fingerprint(thread_id), runtime=runtime,
                   tool=tool, approved=approved)

    def loop_limit(self, thread_id: str, runtime: str, max_tool_turns: int) -> None:
        self._emit("agent_loop_limit", thread_hash=fingerprint(thread_id), runtime=runtime,
                   max_tool_turns=max_tool_turns, failure_scope="runtime")

    def runtime_failure(self, thread_id: str, runtime: str, code: str) -> None:
        self._emit("agent_runtime_error", thread_hash=fingerprint(thread_id), runtime=runtime,
                   error_code=code, failure_scope="runtime")


def _domain_outcome(tool: str, result: dict[str, Any]) -> str:
    payload = result.get("result") if isinstance(result, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("outcome"), str):
        return payload["outcome"]
    return "mutation_applied" if tool in {"add_part", "add_stock", "update_part", "bulk_update_parts", "delete_part", "apply_review", "reject_review"} else "query_completed"
