"""Live measurement exercises real HTTP request assembly with a fake provider."""

import json

import httpx
import pytest

from evaluation.live_lookup import run_lookup


CASE = 'inventory_lookup_value_notation'


def payload(output):
    return {"model": "test-snapshot", "status": "completed", "output": output,
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 40},
                      "output_tokens_details": {"reasoning_tokens": 5}},
            "private_provider_field": "never capture this"}


def answer(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


async def test_real_runtime_measures_http_usage_and_isolated_stock_without_recorded_turns(tmp_path):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body['max_output_tokens'] == 1000
            assert body['input'][0]['content'][0]['text'] == 'Do I have any 0.1 µF 0603 capacitors?'
            assert len(body['input']) == 1
            return httpx.Response(200, json=payload([{
                "type": "function_call", "call_id": "search", "name": "search_parts",
                "arguments": json.dumps({"filters": {"part_category": "capacitor", "value": "0.1 µF", "package": "0603"}}),
            }]))
        assert json.loads(body['input'][-1]['output'])['result']['parts'][0]['quantity'] == 6
        return httpx.Response(200, json=payload([answer('Yes: six (6) in 0603, equivalent to 0.1 µF.')]))

    path = await run_lookup(tmp_path, api_key='private-test-key', model='test-model',
                            scenario_ids=[CASE], http_transport=httpx.MockTransport(respond))
    text = path.read_text()
    report = json.loads(text)
    result = report['results'][0]
    assert result['recorded_contract'] == {'status': 'passed', 'failure': None}
    assert result['inventory_unchanged']
    assert result['provider_attempts'] == 2
    assert result['responses'][0]['model'] == 'test-snapshot'
    assert result['responses'][0]['usage'] == {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 40, 'cache_write_tokens': None, 'reasoning_tokens': 5}
    assert result['semantic_review']['correctness'] == 'needs_review'
    assert result['estimated_cost_usd'] is None
    assert 'private-test-key' not in text and 'never capture this' not in text
    assert all(body['store'] is False for body in requests)
    assert len(list(path.parent.glob('*.db'))) == 2


async def test_failed_contract_preserves_answer_and_continues_other_scenarios(tmp_path):
    path = await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=[CASE, 'inventory_lookup_exact_ordering_suffix'],
                            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload([answer('I guessed without tools.')]))))
    report = json.loads(path.read_text())
    assert report['status'] == 'completed'
    assert len(report['results']) == 2
    for result in report['results']:
        assert result['recorded_contract']['status'] == 'failed'
        assert result['inventory_unchanged']
        assert any(event['data'].get('text') == 'I guessed without tools.' for event in result['events'])


async def test_provider_failure_stops_without_retry_or_saving_provider_error(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, json={'error': {'message': 'private provider details'}})

    path = await run_lookup(tmp_path, api_key='private-key', model='test', http_transport=httpx.MockTransport(respond))
    text = path.read_text()
    report = json.loads(text)
    assert report['status'] == 'provider_failure'
    assert len(requests) == 1 and len(report['results']) == 1
    assert report['results'][0]['recorded_contract']['failure'] == 'HTTPStatusError'
    assert 'private-key' not in text and 'private provider details' not in text


async def test_request_limit_bounds_repeated_model_calls(tmp_path):
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json=payload([{'type': 'function_call', 'call_id': f'call-{count}', 'name': 'search_parts',
            'arguments': json.dumps({'filters': {'part_category': 'capacitor'}})}]))

    path = await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=[CASE], http_transport=httpx.MockTransport(respond))
    result = json.loads(path.read_text())['results'][0]
    assert count == 5
    assert result['provider_attempts'] == 5
    assert result['recorded_contract']['status'] == 'request_limit'


@pytest.mark.parametrize('status', ['incomplete', 'failed'])
async def test_incomplete_provider_output_is_not_a_completed_evaluation(tmp_path, status):
    response = payload([answer('partial text')])
    response['status'] = status
    response['usage'] = None
    path = await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=[CASE],
                            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    result = json.loads(path.read_text())['results'][0]
    assert result['recorded_contract']['status'] == 'provider_failure'
    assert result['responses'][0]['usage']['input_tokens'] is None


async def test_network_failure_records_an_uncertain_attempt_without_retry(tmp_path):
    def respond(request):
        raise httpx.ReadTimeout('private exception details', request=request)
    path = await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=[CASE], http_transport=httpx.MockTransport(respond))
    text = path.read_text()
    result = json.loads(text)['results'][0]
    assert result['provider_attempts'] == 1
    assert result['responses'] == []
    assert result['recorded_contract']['failure'] == 'ReadTimeout'
    assert 'private exception details' not in text


async def test_repeated_runs_preserve_prior_artifacts_and_reject_unknown_ids(tmp_path):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload([answer('No tools.')])) )
    first = await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=[CASE], http_transport=transport)
    second = await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=[CASE], http_transport=transport)
    assert first != second and first.exists() and second.exists()
    with pytest.raises(ValueError, match='Unknown'):
        await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=['missing'], http_transport=transport)


async def test_disallowed_mutation_is_reported_even_when_lookup_state_count_is_not_checked(tmp_path):
    count = 0
    def respond(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(200, json=payload([{'type': 'function_call', 'call_id': 'unsafe', 'name': 'add_part',
                'arguments': json.dumps({'part_category': 'resistor', 'profile': 'passive', 'quantity': 1, 'value': '1k', 'package': '0603'})}]))
        return httpx.Response(200, json=payload([answer('Added a resistor.')]))
    path = await run_lookup(tmp_path, api_key='test', model='test', scenario_ids=[CASE], http_transport=httpx.MockTransport(respond))
    result = json.loads(path.read_text())['results'][0]
    assert result['recorded_contract']['status'] == 'failed'
    assert result['inventory_unchanged'] is False


def test_cli_requires_opt_in_before_constructing_a_provider_client(monkeypatch, tmp_path):
    from evaluation.live_lookup import main
    monkeypatch.delenv('PARTS_BIN_LIVE_EVAL', raising=False)
    monkeypatch.setattr('sys.argv', ['live_lookup', '--workspace', str(tmp_path), '--model', 'test'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())
