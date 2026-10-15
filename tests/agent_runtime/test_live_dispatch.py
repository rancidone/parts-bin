"""Local workers await live model requests while retaining durable stage safety."""

import asyncio
import json
import time

import httpx
import pytest

from agent_runtime import ApprovalResponse, ImageInput, OpenAIResponsesTransport
from application import create_services
from tests.storage import TestRepository
from tests.agent_runtime.test_dispatch import due, row
from tests.agent_runtime.test_background_workers import stock_call


class LiveProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []
        self.error = None
        self.on_request = None

    def handle(self, request):
        self.requests.append(request)
        if self.on_request:
            self.on_request(request)
        if self.error:
            raise self.error
        assert request.method == 'POST'
        assert 'background' not in json.loads(request.content)
        return httpx.Response(200, json={'status': 'completed', 'output': self.outputs.pop(0)})


def build(path, provider, **options):
    repository = TestRepository(path)
    transport = OpenAIResponsesTransport(api_key='test-only', model='test-model',
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)))
    return create_services(repository, repository.conversations, transport_factory=lambda: transport, **options)


async def test_default_local_dispatch_has_no_collection_wait_and_mutates_once(tmp_path):
    provider = LiveProvider([[stock_call()], []])
    services = build(tmp_path, provider)
    assert services.background_responses is False
    thread = services.gateway.create_thread()
    runtime = await services.gateway._runtime_for(thread)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'value': '10k', 'quantity': 1})
    await services.dispatch.submit(thread, 'request', 'add two')
    for expected, quantity in [('ready', 1), ('ready', 3), ('completed', 3)]:
        assert await services.dispatch.run_once()
        record = services.domain.repository.executions.get(thread, 'request')
        assert record.status == expected
        assert services.domain.list()[0].quantity == quantity
        scheduled = row(services.domain.repository)['available_at']
        assert scheduled is None or scheduled <= time.time()
    assert len(provider.requests) == 2
    assert not await services.dispatch.run_once()
    services.dispatch.resume(thread, 'request')
    assert await services.dispatch.run_once()
    assert len(provider.requests) == 2
    assert len([event for event in services.gateway.events(thread) if event.kind == 'tool_result']) == 1
    await services.close()


@pytest.mark.parametrize('error', [httpx.ReadTimeout('private payload'), asyncio.CancelledError()])
async def test_uncertain_live_request_is_never_resubmitted_by_replacement(tmp_path, error):
    provider = LiveProvider([[]])
    provider.error = error
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'hello')
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await services.dispatch.run_once()
    else:
        assert await services.dispatch.run_once()
    await services.close()
    provider.error = None
    replacement = build(tmp_path, provider)
    due(replacement.domain.repository, expire=True)
    assert await replacement.dispatch.run_once()
    assert replacement.gateway.events(thread)[-2].data['code'] == 'model_submission_uncertain'
    assert len(provider.requests) == 1
    await replacement.close()


async def test_live_submission_intent_is_saved_before_contacting_provider(tmp_path):
    provider = LiveProvider([[]])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'hello')
    def inspect(_request):
        state = services.domain.repository.executions.get(thread, 'request').context
        assert state['model_submission_started'] is True and state['model_response_id'] is None
    provider.on_request = inspect
    assert await services.dispatch.run_once()
    assert services.gateway.events(thread)[-1].data['status'] == 'completed'
    await services.close()


async def test_cancellation_during_live_generation_discards_tool_calls(tmp_path):
    provider = LiveProvider([[stock_call(999)]])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'hello')
    provider.on_request = lambda _request: services.domain.repository.executions.request_cancellation(thread, 'request')
    assert await services.dispatch.run_once()
    assert services.gateway.events(thread)[-1].data['status'] == 'cancelled'
    assert not any(event.kind == 'tool_call' for event in services.gateway.events(thread))
    assert not services.domain.list()
    await services.close()


async def test_live_photo_initial_stage_still_has_photo_without_storing_bytes(tmp_path):
    provider = LiveProvider([[]])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'identify', image=ImageInput('image/png', 'private-image'))
    assert len(provider.requests) == 1
    assert 'private-image' in provider.requests[0].content.decode()
    state = services.domain.repository.executions.get(thread, 'request')
    assert state.status == 'completed' and 'private-image' not in json.dumps(state.context)
    await services.close()


async def test_live_changes_still_pause_for_approval_and_replay_once(tmp_path):
    call = {'type': 'function_call', 'name': 'update_part', 'call_id': 'update',
            'arguments': json.dumps({'part_id': 1, 'fields': {'quantity': 20}})}
    provider = LiveProvider([[call], []])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    runtime = await services.gateway._runtime_for(thread)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'value': '10k', 'quantity': 1})
    await services.dispatch.submit(thread, 'request', 'set quantity to 20')
    assert await services.dispatch.run_once()
    assert await services.dispatch.run_once()
    request = next(event for event in services.gateway.events(thread) if event.kind == 'approval_request')
    assert services.domain.list()[0].quantity == 1
    services.dispatch.decide(thread, ApprovalResponse(request.data['request_id'], True))
    assert await services.dispatch.run_once()
    assert await services.dispatch.run_once()
    assert services.domain.list()[0].quantity == 20
    assert len(provider.requests) == 2
    await services.close()


@pytest.mark.parametrize('value', ['false', 0, 1, None])
def test_model_delivery_setting_rejects_non_booleans(tmp_path, value):
    with pytest.raises(ValueError, match='background_responses'):
        build(tmp_path, LiveProvider([]), background_responses=value)


async def test_switch_to_live_mode_collects_already_submitted_background_response(tmp_path):
    from tests.agent_runtime.test_background_workers import Provider
    from tests.agent_runtime.test_dispatch import build as background_services
    provider = Provider([{'status': 'completed', 'output': []}])
    first = background_services(tmp_path, provider)
    thread = first.gateway.create_thread()
    await first.dispatch.submit(thread, 'request', 'hello')
    assert await first.dispatch.run_once()
    assert first.domain.repository.executions.get(thread, 'request').status == 'waiting_model'
    await first.close()
    replacement = build(tmp_path, provider)
    due(replacement.domain.repository)
    assert await replacement.dispatch.run_once()
    assert replacement.gateway.events(thread)[-1].data['status'] == 'completed'
    assert provider.posts == 1
    assert [request.method for request in provider.requests] == ['POST', 'GET']
    await replacement.close()
