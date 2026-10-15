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

from agent_runtime import ApprovalEngine, ImageInput, OpenAIResponsesRuntime, OpenAIResponsesTransport
from agent_runtime.budgets import ModelBudgetError
from photo.pipeline import preprocess
from domain import PartsBinService
from tools import PartsBinToolRegistry
from .tool_efficiency import assess_tool_efficiency
from .runner import (
    EvaluationFailure, INVENTORY_LOOKUP_SCENARIOS_PATH, _snapshot,
    live_enabled, load_scenarios, run_scenario,
)


class RequestLimitError(RuntimeError):
    pass


class IncompleteResponseError(RuntimeError):
    pass


class BackgroundScenarioRuntime:
    """Exercise independent bounded runtime stages, without a second agent loop."""

    def __init__(self, factory, *, poll_seconds: float, timeout_seconds: float, photo: ImageInput | None = None):
        self.factory = factory
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.stages = []
        self.photo = photo

    async def run(self, thread_id, user_text, *, image=None, approval_response=None):
        if approval_response is not None:
            raise EvaluationFailure("Background lookup measurements do not approve mutations")
        if image is not None:
            if self.photo is None:
                raise ValueError("A real photo is required for live image scenarios")
            image = self.photo
        runtime = self.factory()
        execution_id = runtime.prepare(thread_id, uuid4().hex, user_text, image=image)
        started = perf_counter()
        for _ in range(256):
            if perf_counter() - started >= self.timeout_seconds:
                raise IncompleteResponseError("Background evaluation deadline reached; no resubmission")
            # A new runtime observes the same persisted checkpoint at every stage.
            result = await self.factory().advance_background(thread_id, execution_id,
                image=image, approval_response=approval_response)
            self.stages.append({"status": result.status,
                "elapsed_ms": round((perf_counter() - started) * 1000, 1)})
            if result.status not in {"ready", "waiting_model"}:
                return result
            if result.status == "waiting_model":
                await asyncio.sleep(self.poll_seconds)
        raise IncompleteResponseError("Background evaluation stage limit reached")


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
                     http_transport: httpx.AsyncBaseTransport | None = None,
                     background: bool = False, poll_seconds: float = 30,
                     timeout_seconds: float = 540, probe_after_seconds: float | None = None,
                     scenarios_path: Path = INVENTORY_LOOKUP_SCENARIOS_PATH,
                     photo_path: Path | None = None) -> Path:
    """Measure the production runtime, ignoring every fixture's recorded turns.

    An injected HTTP transport is only for offline tests. The CLI always contacts
    the standard OpenAI endpoint. No local application configuration is loaded.
    """
    if http_transport is None and not live_enabled():
        raise ValueError('Set PARTS_BIN_LIVE_EVAL=1 to opt in to paid requests')
    if not api_key.strip() or not model.strip():
        raise ValueError("An OpenAI API key and an explicit model are required")
    if not 0 <= poll_seconds <= 60 or not 0 < timeout_seconds <= 540:
        raise ValueError("Poll interval must be 0–60 seconds and deadline 0–540 seconds")
    if probe_after_seconds is not None and (not background or not 0 <= probe_after_seconds <= 900):
        raise ValueError("Retention probes require background mode and a delay of 0–900 seconds")
    scenarios = load_scenarios(scenarios_path)
    known_ids = {item["id"] for item in scenarios}
    if scenario_ids and set(scenario_ids) - known_ids:
        raise ValueError("Unknown lookup scenario ID")
    scenarios = [item for item in scenarios if not scenario_ids or item["id"] in scenario_ids]
    has_images = any(turn.get('image') for item in scenarios for turn in item['conversation'])
    if has_images and (not background or photo_path is None):
        raise ValueError("Live image scenarios require --background and --photo")
    photo = ImageInput('image/jpeg', preprocess(photo_path.read_bytes())) if has_images else None
    run_dir = workspace / f"live-lookup-{uuid4().hex}"
    run_dir.mkdir(parents=True)
    report_path = run_dir / "report.json"
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "requested_model": model,
        "code_sha256": {str(path.relative_to(Path(__file__).resolve().parent.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for directory in ("agent_runtime", "domain", "tools", "db", "evaluation", "photo")
                        for path in sorted((Path(__file__).resolve().parent.parent / directory).rglob("*.py"))},
        "fixture_sha256": hashlib.sha256(scenarios_path.read_bytes()).hexdigest(),
        "has_photo": has_images,
        "max_requests_per_scenario": 8,
        "execution_mode": "background" if background else "foreground",
        "poll_seconds": poll_seconds if background else None,
        "retention_probe": {"status": "not_requested" if probe_after_seconds is None else "pending"},
        "status": "running", "results": [],
        "review_note": "Recorded-contract checks are mechanical. Inspect answers and tool results for correctness and clarification usefulness.",
    }

    def save():
        pending = report_path.with_suffix('.tmp')
        pending.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        pending.replace(report_path)

    save()
    probe_target = None
    for recorded_scenario in scenarios:
        scenario = deepcopy(recorded_scenario)
        scenario['tool_constraints'].update(scenario.get('live_tool_constraints', {}))
        calls = []
        attempts = 0
        storage = {}
        response_indices = {}
        report['active_scenario'] = {'scenario_id': scenario['id'], 'responses': calls}

        async def before_request(request):
            nonlocal attempts
            if request.method == "POST":
                if attempts >= 8:
                    raise RequestLimitError("Scenario exceeded eight provider requests")
                attempts += 1
            request.extensions["evaluation_started"] = perf_counter()
            request.extensions["measurement_index"] = len(calls)
            calls.append({"operation": "submission" if request.method == "POST" else "collection",
                          "submission_index": attempts if request.method == 'POST' else response_indices.get(request.url.path.split('/')[-1]),
                          "elapsed_ms": round((perf_counter() - started) * 1000, 1),
                          "outcome": "uncertain_submission" if request.method == "POST" else "retrieval_failure"})
            save()

        async def after_response(response):
            nonlocal probe_target
            await response.aread()
            measurement = response_measurement(response)
            measurement["latency_ms"] = round((perf_counter() - response.request.extensions["evaluation_started"]) * 1000, 1)
            call = calls[response.request.extensions["measurement_index"]]
            call.update(measurement, outcome="received")
            if not response.is_success:
                call['outcome'] = 'submission_rejected' if response.request.method == 'POST' else 'retrieval_failure'
            if background and response.is_success and response.request.method == "POST" and probe_target is None:
                probe_target = (response.json().get("id"), response.request.extensions["evaluation_started"])
            if background and response.is_success and response.request.method == 'POST':
                response_indices[response.json().get('id')] = call['submission_index']
            save()
            if not background and response.is_success and measurement.get("status") != "completed":
                raise IncompleteResponseError("Provider response did not complete")

        started = perf_counter()
        status, failure = "passed", None
        async with httpx.AsyncClient(transport=http_transport, timeout=60,
                                    event_hooks={"request": [before_request], "response": [after_response]}) as client:
            def factory(runtime, repository, conversations, turns):
                # Supplied repositories contain only this scenario's seeded stock.
                storage.update(repository=repository, conversations=conversations, before=_snapshot(repository))
                transport = OpenAIResponsesTransport(api_key=api_key, model=model, client=client)
                def stage_factory():
                    return OpenAIResponsesRuntime(transport,
                        registry=PartsBinToolRegistry(PartsBinService(repository)),
                        store=conversations, approvals=ApprovalEngine(repository), max_tool_turns=9)
                instance = BackgroundScenarioRuntime(stage_factory, poll_seconds=poll_seconds,
                    timeout_seconds=timeout_seconds, photo=photo) if background else stage_factory()
                storage['runtime'] = instance
                return instance, transport

            try:
                await run_scenario(scenario, "openai", run_dir, factory=factory)
            except EvaluationFailure as error:
                status, failure = "failed", str(error)
            except RequestLimitError:
                status, failure = "request_limit", "Scenario exceeded eight provider requests"
            except (httpx.HTTPError, IncompleteResponseError, ModelBudgetError) as error:
                # Exception text may include provider data; store only its class.
                status, failure = "provider_failure", type(error).__name__

        events = storage["conversations"].events(f"eval-{scenario['id']}")
        if background and any(stage['status'] == 'failed' for stage in storage['runtime'].stages):
            status, failure = 'provider_failure', 'Background execution failed; inspect saved events before another paid request'
        unchanged = storage["before"] == _snapshot(storage["repository"])
        report["results"].append({
            "scenario_id": scenario["id"],
            "recorded_contract": {"status": status, "failure": failure},
            "inventory_unchanged": unchanged,
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "provider_attempts": attempts, "responses": calls,
            "background_stages": storage['runtime'].stages if background else [],
            "estimated_cost_usd": None,
            "cost_note": "Use returned model snapshots and usage with current account pricing; missing usage is unknown, not zero.",
            "events": [event.payload() for event in events],
            "tool_efficiency": assess_tool_efficiency([event.payload() for event in events]),
            "semantic_review": {"correctness": "needs_review", "clarification_usefulness": "needs_review"},
        })
        report.pop('active_scenario', None)
        save()
        # Authentication, unavailable models and uncertain paid requests need an
        # explicit operator retry, not another scenario's automatic request.
        if status == "provider_failure":
            report["status"] = "provider_failure"
            break
    else:
        report["status"] = "completed"
    if probe_after_seconds is not None and probe_target is not None:
        response_id, submitted_at = probe_target
        await asyncio.sleep(max(0, submitted_at + probe_after_seconds - perf_counter()))
        # Observation only: never resubmit or change store=false to extend retention.
        async with httpx.AsyncClient(transport=http_transport, timeout=60) as client:
            transport = OpenAIResponsesTransport(api_key=api_key, model=model, client=client)
            probe = {"age_seconds": round(perf_counter() - submitted_at, 1)}
            try:
                turn = await transport.retrieve(response_id)
                probe['status'] = 'available' if turn is not None else 'pending'
            except ModelBudgetError as error:
                probe['status'] = 'unavailable' if error.code == 'model_response_unavailable' else 'invalid'
            except httpx.HTTPError as error:
                probe.update(status='retrieval_failure', failure=type(error).__name__)
            report['retention_probe'] = probe
    elif probe_after_seconds is not None:
        report['retention_probe'] = {'status': 'no_confirmed_submission'}
    save()
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--model', default=os.environ.get('PARTS_BIN_OPENAI_MODEL'))
    parser.add_argument('--scenario', action='append', help='Run selected lookup scenario IDs')
    parser.add_argument('--background', action='store_true', help='Submit and collect through persisted background stages')
    parser.add_argument('--scenarios', type=Path, default=INVENTORY_LOOKUP_SCENARIOS_PATH,
                        help='Reviewed scenario file, seeded only in disposable storage')
    parser.add_argument('--photo', type=Path, help='Actual photo for background scenarios with image=true; never saved in reports')
    parser.add_argument('--poll-seconds', type=float, default=30)
    parser.add_argument('--probe-after-seconds', type=float, help='Observe the first response again at this age, without resubmission')
    args = parser.parse_args()
    if not live_enabled():
        parser.error('Set PARTS_BIN_LIVE_EVAL=1 to opt in to paid requests')
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key or not args.model:
        parser.error('Set OPENAI_API_KEY and specify --model or PARTS_BIN_OPENAI_MODEL')
    report_path = asyncio.run(run_lookup(args.workspace, api_key=key, model=args.model, scenario_ids=args.scenario,
        background=args.background, poll_seconds=args.poll_seconds, probe_after_seconds=args.probe_after_seconds,
        scenarios_path=args.scenarios, photo_path=args.photo))
    report = json.loads(report_path.read_text())
    print(json.dumps({"report": str(report_path), "status": report['status'],
                      "results": [{"scenario_id": row['scenario_id'], **row['recorded_contract']} for row in report['results']]}))
    raise SystemExit(0 if report['status'] == 'completed' and all(
        row['recorded_contract']['status'] == 'passed' and row['inventory_unchanged'] for row in report['results']) else 1)


if __name__ == '__main__':
    from db.postgres.disposable import cleanup
    try:
        main()
    finally:
        cleanup()
