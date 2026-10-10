import json
from unittest.mock import AsyncMock

import httpx
import pytest

from evaluation.live_electrical import load_cases, run_electrical
from ingestion.supplied_source import Document


def document(case):
    return Document(case['url'], 'a' * 64, '2026-10-10T00:00:00+00:00', (
        f"{case['part_number']} {case['manufacturer']}. 10 kΩ, 1 %, 2.5 W at 25 °C.",))


def response(case):
    excerpt = document(case).pages[0]
    fields = {'part_number': case['part_number'], 'manufacturer': case['manufacturer']}
    return {'outcome': 'proposal', 'clarification': None, 'mismatch_evidence': None, 'fields': {
        **{name: {'value': value, 'evidence': {'page': 1, 'excerpt': excerpt}}
           for name, value in fields.items()}, 'package': None, 'description': None},
        'facts': [{'name': name, 'value': value, 'basis': basis,
                   'conditions': [{'name': 'ambient_temperature', 'value': '25 °C'}] if name == 'rated_power' else [],
                   'evidence': {'passage_ids': [1]}}
                  for name, value, basis in [('resistance', '10 kΩ', 'nominal'),
                      ('tolerance', '1 %', 'maximum'), ('rated_power', '2.5 W', 'rated')]]}


async def test_offline_transport_is_labeled_and_measures_one_request_without_credentials(tmp_path):
    case = load_cases(['four_10k_resistors'])[0]
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'completed', 'model': 'test-snapshot',
            'usage': {'input_tokens': 123, 'output_tokens': 45}, 'output': [
                {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(response(case))}]}]})
    path = await run_electrical(tmp_path, api_key='do-not-record-secret', model='test',
        case_ids=[case['id']], http_transport=httpx.MockTransport(handler),
        retriever=AsyncMock(return_value=document(case)))
    report = json.loads(path.read_text())
    assert report['mode'] == 'offline_transport' and report['status'] == 'completed'
    row = report['results'][0]
    assert len(calls) == row['provider_attempts'] == 1
    assert all(row['workflow']['checks'].values()) and row['inventory_preserved']
    assert row['responses'][0]['model'] == 'test-snapshot'
    assert row['responses'][0]['usage']['input_tokens'] == 123
    assert row['semantic_review']['status'] == 'needs_review'
    assert 'do-not-record-secret' not in path.read_text()
    assert 'Authorization' not in path.read_text() and 'pages' not in row.get('source', {})


async def test_retrieval_failure_does_not_call_model_or_become_no_match(tmp_path):
    def handler(_):
        pytest.fail('Retrieval failure must not call the model')
    path = await run_electrical(tmp_path, api_key='fake', model='test',
        case_ids=['retrieval_failure_nexperia'], http_transport=httpx.MockTransport(handler),
        retriever=AsyncMock(side_effect=TimeoutError))
    row = json.loads(path.read_text())['results'][0]
    assert row['outcome'] == 'retrieval_failure' and row['provider_attempts'] == 0
    assert row['candidate'] is None and row['inventory_preserved']


async def test_provider_authentication_failure_stops_without_retry_or_error_body(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={'error': 'private-provider-content'})
    case = load_cases(['four_10k_resistors'])[0]
    path = await run_electrical(tmp_path, api_key='fake', model='test',
        case_ids=['four_10k_resistors', 'capacitor_voltage_conditions'],
        http_transport=httpx.MockTransport(handler), retriever=AsyncMock(return_value=document(case)))
    report = json.loads(path.read_text())
    assert report['status'] == 'provider_failure' and len(calls) == len(report['results']) == 1
    assert report['results'][0]['failure']['http_status'] == 401
    assert 'private-provider-content' not in path.read_text()
    assert report['results'][0]['responses'][0] == {'http_status': 401,
        'latency_ms': report['results'][0]['responses'][0]['latency_ms']}


async def test_saved_link_evaluation_passes_association_and_preserves_stock(tmp_path):
    from unittest.mock import patch
    case = load_cases(['opamp_saved_link_missing_suffix'])[0]
    candidate = {'outcome': 'proposal', 'facts': []}
    with patch('evaluation.live_electrical.source.extract', AsyncMock(return_value=candidate)) as extract:
        path = await run_electrical(tmp_path, api_key='fake', model='test', case_ids=[case['id']],
            http_transport=httpx.MockTransport(lambda _: pytest.fail('No provider call expected')),
            retriever=AsyncMock(return_value=document(case)))
    assert extract.call_args.kwargs['linked_part'] is True
    row = json.loads(path.read_text())['results'][0]
    assert row['linked_part'] and row['inventory_preserved']
