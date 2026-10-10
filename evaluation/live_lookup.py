"""Opt-in live lookup measurement using synthetic scenarios and isolated stores."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from agent_runtime import ApprovalEngine, OpenAIResponsesRuntime, OpenAIResponsesTransport
from domain import PartsBinService
from tools import PartsBinToolRegistry
from .runner import (
    EvaluationFailure, INVENTORY_LOOKUP_SCENARIOS_PATH, _snapshot,
    live_enabled, load_scenarios, run_scenario,
)


class RequestLimitError(RuntimeError):
    pass


class IncompleteResponseError(RuntimeError):
    pass


def _token_count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def response_measurement(response: httpx.Response) -> dict:
    """Retain fixed usage fields, never headers, provider errors or reasoning."""
    result = {"http_status": response.status_code}
    if not response.is_success:
        return result
    payload = response.json()
    result.update(model=payload.get("model"), status=payload.get("status"))
    usage = payload.get("usage") or {}
    result["usage"] = {
        "input_tokens": _token_count(usage.get("input_tokens")),
        "output_tokens": _token_count(usage.get("output_tokens")),
        "cached_input_tokens": _token_count((usage.get("input_tokens_details") or {}).get("cached_tokens")),
        "cache_write_tokens": _token_count((usage.get("input_tokens_details") or {}).get("cache_write_tokens")),
        "reasoning_tokens": _token_count((usage.get("output_tokens_details") or {}).get("reasoning_tokens")),
    }
    return result


async def run_lookup(workspace: Path, *, api_key: str, model: str,
                     scenario_ids: list[str] | None = None,
                     http_transport: httpx.AsyncBaseTransport | None = None) -> Path:
    """Measure the production runtime, ignoring every fixture's recorded turns.

    An injected HTTP transport is only for offline tests. The CLI always contacts
    the standard OpenAI endpoint. No local application configuration is loaded.
    """
    if not api_key.strip() or not model.strip():
        raise ValueError("An OpenAI API key and an explicit model are required")
    scenarios = load_scenarios(INVENTORY_LOOKUP_SCENARIOS_PATH)
    known_ids = {item["id"] for item in scenarios}
    if scenario_ids and set(scenario_ids) - known_ids:
        raise ValueError("Unknown lookup scenario ID")
    scenarios = [item for item in scenarios if not scenario_ids or item["id"] in scenario_ids]
    run_dir = workspace / f"live-lookup-{uuid4().hex}"
    run_dir.mkdir(parents=True)
    report_path = run_dir / "report.json"
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "requested_model": model,
        "code_sha256": {str(path.relative_to(Path(__file__).resolve().parent.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for directory in ("agent_runtime", "domain", "tools", "db", "evaluation")
                        for path in sorted((Path(__file__).resolve().parent.parent / directory).glob("*.py"))},
        "fixture_sha256": hashlib.sha256(INVENTORY_LOOKUP_SCENARIOS_PATH.read_bytes()).hexdigest(),
        "max_requests_per_scenario": 8,
        "status": "running", "results": [],
        "review_note": "Recorded-contract checks are mechanical. Inspect answers and tool results for correctness and clarification usefulness.",
    }

    def save():
        pending = report_path.with_suffix('.tmp')
        pending.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        pending.replace(report_path)

    save()
    for recorded_scenario in scenarios:
        scenario = deepcopy(recorded_scenario)
        scenario['tool_constraints'].update(scenario.get('live_tool_constraints', {}))
        calls = []
        attempts = 0
        storage = {}

        async def before_request(request):
            nonlocal attempts
            if attempts >= 8:
                raise RequestLimitError("Scenario exceeded eight provider requests")
            attempts += 1
            request.extensions["evaluation_started"] = perf_counter()

        async def after_response(response):
            await response.aread()
            measurement = response_measurement(response)
            measurement["latency_ms"] = round((perf_counter() - response.request.extensions["evaluation_started"]) * 1000, 1)
            calls.append(measurement)
            if response.is_success and measurement.get("status") != "completed":
                raise IncompleteResponseError("Provider response did not complete")

        started = perf_counter()
        status, failure = "passed", None
        async with httpx.AsyncClient(transport=http_transport, timeout=60,
                                    event_hooks={"request": [before_request], "response": [after_response]}) as client:
            def factory(runtime, repository, conversations, turns):
                # Supplied repositories contain only this scenario's seeded stock.
                storage.update(repository=repository, conversations=conversations, before=_snapshot(repository))
                transport = OpenAIResponsesTransport(api_key=api_key, model=model, client=client)
                return OpenAIResponsesRuntime(transport,
                    registry=PartsBinToolRegistry(PartsBinService(repository)),
                    store=conversations, approvals=ApprovalEngine(repository), max_tool_turns=9), transport

            try:
                await run_scenario(scenario, "openai", run_dir, factory=factory)
            except EvaluationFailure as error:
                status, failure = "failed", str(error)
            except RequestLimitError:
                status, failure = "request_limit", "Scenario exceeded eight provider requests"
            except (httpx.HTTPError, IncompleteResponseError) as error:
                # Exception text may include provider data; store only its class.
                status, failure = "provider_failure", type(error).__name__

        events = storage["conversations"].events(f"eval-{scenario['id']}")
        unchanged = storage["before"] == _snapshot(storage["repository"])
        report["results"].append({
            "scenario_id": scenario["id"],
            "recorded_contract": {"status": status, "failure": failure},
            "inventory_unchanged": unchanged,
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "provider_attempts": attempts, "responses": calls,
            "estimated_cost_usd": None,
            "cost_note": "Use returned model snapshots and usage with current account pricing; missing usage is unknown, not zero.",
            "events": [event.payload() for event in events],
            "semantic_review": {"correctness": "needs_review", "clarification_usefulness": "needs_review"},
        })
        save()
        # Authentication, unavailable models and uncertain paid requests need an
        # explicit operator retry, not another scenario's automatic request.
        if status == "provider_failure":
            report["status"] = "provider_failure"
            break
    else:
        report["status"] = "completed"
    save()
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--model', default=os.environ.get('PARTS_BIN_OPENAI_MODEL'))
    parser.add_argument('--scenario', action='append', help='Run selected lookup scenario IDs')
    args = parser.parse_args()
    if not live_enabled():
        parser.error('Set PARTS_BIN_LIVE_EVAL=1 to opt in to paid requests')
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key or not args.model:
        parser.error('Set OPENAI_API_KEY and specify --model or PARTS_BIN_OPENAI_MODEL')
    report_path = asyncio.run(run_lookup(args.workspace, api_key=key, model=args.model, scenario_ids=args.scenario))
    report = json.loads(report_path.read_text())
    print(json.dumps({"report": str(report_path), "status": report['status'],
                      "results": [{"scenario_id": row['scenario_id'], **row['recorded_contract']} for row in report['results']]}))
    raise SystemExit(0 if report['status'] == 'completed' and all(
        row['recorded_contract']['status'] == 'passed' and row['inventory_unchanged'] for row in report['results']) else 1)


if __name__ == '__main__':
    main()
