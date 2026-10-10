"""Bounded live supplied-source evaluation against disposable inventory."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from agent_runtime import ApprovalEngine
from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, DomainError, GetPartRequest, PartFields, PartsBinService, SearchPartsRequest
from ingestion import supplied_source as source
from tools import PartsBinToolRegistry
from .live_lookup import response_measurement
from .runner import live_enabled

CASES = Path(__file__).parent / 'enrichment' / 'electrical_sources.json'


def same_stock(left, right) -> bool:
    # Accepting evidence updates updated_at; all authoritative stock fields stay fixed.
    return {k: v for k, v in vars(left).items() if k != 'updated_at'} == {
        k: v for k, v in vars(right).items() if k != 'updated_at'}


def load_cases(case_ids: list[str] | None = None) -> list[dict]:
    cases = json.loads(CASES.read_text())['cases']
    if case_ids and set(case_ids) - {case['id'] for case in cases}:
        raise ValueError('Unknown electrical source case ID')
    return [case for case in cases if not case_ids or case['id'] in case_ids]


async def exercise_review(database: Path, case: dict, candidate: dict) -> dict:
    """Exercise approval mechanics in isolated seeded stock, not certify semantics."""
    service = PartsBinService(SQLitePartsBinRepository(database))
    part = service.list()[0]
    facts = candidate['facts']
    before = service.get_specifications(part.id)
    service.stage_specifications(part, facts)
    staged = service.get_specifications(part.id)
    registry = PartsBinToolRegistry(service)
    denied = await registry.execute('apply_specification_review', {'part_id': part.id})
    engine = ApprovalEngine(service.repository)
    request = engine.request('electrical-evaluation', 'apply_specification_review',
                             {'part_id': part.id}, service=service)
    # Reconstruct repositories while awaiting approval, as a worker replacement would.
    restarted = PartsBinService(SQLitePartsBinRepository(database))
    engine = ApprovalEngine(restarted.repository)
    approved = engine.decide(request.thread_id, request.request_id, True)
    accepted = await engine.execute(approved, PartsBinToolRegistry(restarted))
    replay = await engine.execute(approved, PartsBinToolRegistry(restarted))
    after = restarted.get_specifications(part.id)
    lookup = None
    if case['category'] == 'resistor':
        by_name = {fact['name']: fact for fact in facts}
        required = [('resistance', 'eq', '10 kΩ'), ('tolerance', 'lte', '1 %'), ('rated_power', 'gte', '0.25 W')]
        if all(name in by_name for name, _, _ in required):
            requirements = [{'name': name, 'comparison': op, 'value': value,
                             'basis': by_name[name]['basis'], 'conditions': by_name[name]['conditions']}
                            for name, op, value in required]
            lookup = restarted.search_specifications(SearchPartsRequest(
                {'part_category': 'resistor', 'value': '10k'}, 4), requirements)
            lookup['requirements'] = requirements
    checks = {
        'pending_separate_from_accepted': before['facts'] == staged['facts'] == [],
        'approval_required': denied.get('error', {}).get('code') == 'approval_required',
        'approval_applied': accepted['ok'],
        'replay_same_result': replay == accepted,
        'evidence_preserved': after['facts'] == facts and after['pending_review'] is None,
        'identity_quantity_preserved': same_stock(restarted.get(GetPartRequest(part.id)), part),
    }
    if case['id'] == 'four_10k_resistors':
        checks['four_resistor_requirements_confirmed'] = lookup is not None and lookup['match_count'] == 1
    return {'checks': checks, 'lookup': lookup,
            'note': 'Approval here tests disposable workflow mechanics; semantic correctness requires source review.'}


async def run_electrical(workspace: Path, *, api_key: str, model: str,
                         case_ids: list[str] | None = None,
                         http_transport: httpx.AsyncBaseTransport | None = None,
                         retriever=None) -> Path:
    if not api_key.strip() or not model.strip():
        raise ValueError('An API key and explicit model are required')
    cases = load_cases(case_ids)
    retriever = retriever or source.retrieve_pdf
    run_dir = workspace / f'electrical-{uuid4().hex}'
    run_dir.mkdir(parents=True)
    path = run_dir / 'report.json'
    root = Path(__file__).resolve().parent.parent
    report = {
        'started_at': datetime.now(timezone.utc).isoformat(), 'requested_model': model,
        'mode': 'live' if http_transport is None else 'offline_transport',
        'policy_version': source.POLICY_VERSION,
        'fixture_sha256': hashlib.sha256(CASES.read_bytes()).hexdigest(),
        'code_sha256': {str(file.relative_to(root)): hashlib.sha256(file.read_bytes()).hexdigest()
                        for directory in ('ingestion', 'domain', 'tools', 'db', 'evaluation')
                        for file in sorted((root / directory).glob('*.py'))},
        'max_requests_per_case': 1, 'status': 'running', 'results': [],
    }

    def save():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        temporary.replace(path)

    save()
    for case in cases:
        database = run_dir / f"{case['id']}.db"
        service = PartsBinService(SQLitePartsBinRepository(database))
        part = service.add_part(AddPartRequest(PartFields(case['category'],
            'passive' if case['category'] in {'resistor', 'capacitor', 'inductor'} else 'discrete_ic',
            case['quantity'], value=case['value'], part_number=case['part_number'], manufacturer=case['manufacturer'],
            datasheet_url=case['url'] if case.get('linked_part') else None)))
        row = {'case_id': case['id'], 'source_url': case['url'], 'expected_outcome': case['expected_outcome'],
               'linked_part': case.get('linked_part', False),
               'provider_attempts': 0, 'responses': [], 'candidate': None,
               'semantic_review': {'status': 'needs_review', 'instructions': case['review']},
               'estimated_cost_usd': None,
               'cost_note': 'Use returned model and measured usage with current account pricing; unknown usage is not zero.'}
        started = perf_counter()

        async def before_request(request):
            if request.url.host == 'api.openai.com':
                if row['provider_attempts'] >= 1:
                    raise source.EnrichmentError('Case exceeded one model request')
                row['provider_attempts'] += 1
            request.extensions['evaluation_started'] = perf_counter()

        async def after_response(response):
            if response.request.url.host != 'api.openai.com':
                return
            await response.aread()
            measurement = response_measurement(response)
            measurement['latency_ms'] = round((perf_counter() - response.request.extensions['evaluation_started']) * 1000, 1)
            row['responses'].append(measurement)
            if response.is_success:
                # Public source candidates only; never store headers, reasoning or error bodies.
                row['raw_candidate_text'] = ''.join(block.get('text', '')
                    for item in response.json().get('output', []) if item.get('type') == 'message'
                    for block in item.get('content', []) if block.get('type') == 'output_text')[:100_000]

        stage = 'retrieval'
        async with httpx.AsyncClient(timeout=60, trust_env=False, transport=http_transport,
                                    event_hooks={'request': [before_request], 'response': [after_response]}) as client:
            try:
                document = await retriever(case['url'], client)
                excerpts, omitted = source.select_excerpts(document, case['part_number'])
                tables, table_omission = source.relevant_tables(document.tables, case['part_number'], excerpts=excerpts)
                excerpts, passage_omission = source.electrical_passages(excerpts,
                    budget=source.MAX_EXCERPT_BYTES - (source.MAX_TABLE_CONTEXT_BYTES if tables else 0))
                omitted = omitted or table_omission
                omitted = omitted or passage_omission
                row['source'] = {'url': document.url, 'sha256': document.sha256,
                                 'retrieved_at': document.retrieved_at, 'page_count': len(document.pages),
                                 'selected_text_bytes': sum(len(item['text'].encode()) for item in excerpts),
                                 'selected_table_context_bytes': len(json.dumps(tables, ensure_ascii=False).encode()),
                                 'selected_pages': sorted({item['page'] for item in excerpts}),
                                 'text_omitted': omitted}
                row['retrieval_latency_ms'] = round((perf_counter() - started) * 1000, 1)
                stage = 'extraction'
                candidate = await source.extract(document, case['part_number'], case['manufacturer'],
                    category=case['category'], api_key=api_key, model=model, client=client,
                    linked_part=case.get('linked_part', False))
                row.update(outcome=candidate['outcome'], candidate=candidate)
                if candidate['facts']:
                    stage = 'review'
                    row['workflow'] = await exercise_review(database, case, candidate)
            except (source.EnrichmentError, DomainError, httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
                row.update(outcome=f'{stage}_failure', failure={'type': type(exc).__name__})
                if isinstance(exc, httpx.HTTPStatusError):
                    row['failure']['http_status'] = exc.response.status_code
                elif isinstance(exc, source.EnrichmentError):
                    row['failure']['message'] = str(exc)
        row['inventory_preserved'] = same_stock(service.get(GetPartRequest(part.id)), part)
        row['latency_ms'] = round((perf_counter() - started) * 1000, 1)
        row['outcome_matches_expectation'] = row['outcome'] == case['expected_outcome']
        report['results'].append(row)
        save()
        # Stop authentication/availability failures; no automatic paid retries.
        if row['outcome'] == 'extraction_failure' and row.get('failure', {}).get('http_status'):
            report['status'] = 'provider_failure'
            break
    else:
        report['status'] = 'completed'
    save()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    if not live_enabled():
        parser.error('Set PARTS_BIN_LIVE_EVAL=1 to opt in to paid requests')
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key:
        parser.error('Set OPENAI_API_KEY; uv run --env-file .env can load local configuration')
    path = asyncio.run(run_electrical(args.workspace, api_key=key, model=args.model, case_ids=args.case))
    report = json.loads(path.read_text())
    print(json.dumps({'report': str(path), 'status': report['status'], 'results': [
        {'case_id': row['case_id'], 'outcome': row['outcome'], 'mechanical_expectation': row['outcome_matches_expectation']}
        for row in report['results']]}))
    # This command never certifies semantic correctness from JSON or tool success.
    raise SystemExit(2 if report['status'] == 'completed' else 1)


if __name__ == '__main__':
    main()
