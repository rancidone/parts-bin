"""Cancellation boundaries, provider cleanup, and committed-effect recovery."""

import json

import httpx
import pytest

from agent_runtime import ApprovalResponse, ImageInput
from domain import GetPartRequest
from tests.agent_runtime.test_background_workers import Provider, stock_call, worker_at


class CancelProvider(Provider):
    def __init__(self, responses=(), outcomes=("cancelled",)):
        super().__init__(responses)
        self.outcomes = list(outcomes)
        self.cancels = 0
        self.on_start = None

    def handle(self, request):
        if request.url.path.endswith('/cancel'):
            self.requests.append(request)
            self.cancels += 1
            outcome = self.outcomes[min(self.cancels - 1, len(self.outcomes) - 1)]
            if isinstance(outcome, int):
                return httpx.Response(outcome)
            return httpx.Response(200, json={"id": request.url.path.split('/')[-2], "status": outcome,
                                           "output": [stock_call(100)]})
        result = super().handle(request)
        if request.method == 'POST' and self.on_start:
            self.on_start()
        return result


def setup(tmp_path, provider):
    runtime, transport, _ = worker_at(tmp_path, provider)
    runtime.store = runtime.registry.service.repository.conversations
    return runtime, transport, runtime.registry.service.repository


@pytest.mark.parametrize('image', [None, ImageInput('image/png', 'private')])
async def test_cancel_before_submission_is_durable_idempotent_and_needs_no_photo(tmp_path, image):
    provider = CancelProvider()
    runtime, transport, repo = setup(tmp_path, provider)
    runtime.prepare('thread', 'work', 'hello', image=image, durable=True)
    assert repo.executions.request_cancellation('thread', 'work') == 'cancelling'
    assert repo.executions.request_cancellation('thread', 'work') == 'cancelling'
    assert len([e for e in repo.conversations.events('thread') if e.kind == 'cancellation_requested']) == 1
    await transport.close()
    replacement, transport, repo = setup(tmp_path, provider)
    result = await replacement.advance_background('thread', 'work')
    assert result.status == 'cancelled'
    assert result.events[-1].data['provider_outcome'] == 'no_inflight_response'
    assert not provider.requests
    assert repo.executions.request_cancellation('thread', 'work') == 'cancelled'
    assert await replacement.advance_background('thread', 'work') == result
    replacement.prepare('thread', 'new', 'a new request')
    await transport.close()


@pytest.mark.parametrize('outcome', ['cancelled', 'completed', 'failed', 'incomplete', 404, 410])
async def test_cancel_known_response_discards_every_provider_tool_even_if_completed(tmp_path, outcome):
    provider = CancelProvider(outcomes=[outcome])
    runtime, transport, repo = setup(tmp_path, provider)
    runtime.prepare('thread', 'work', 'hello', durable=True)
    await runtime.advance_background('thread', 'work')
    repo.executions.request_cancellation('thread', 'work')
    result = await runtime.advance_background('thread', 'work')
    assert result.status == 'cancelled'
    assert result.events[-1].data['provider_outcome'] == ('unavailable' if isinstance(outcome, int) else outcome)
    assert not any(e.kind == 'tool_call' for e in result.events)
    assert provider.posts == provider.cancels == 1
    assert repo.executions.get('thread', 'work').context['model_response_id'] == 'resp_1'
    await transport.close()


async def test_cancel_arriving_inside_submission_keeps_response_id_for_cleanup(tmp_path):
    provider = CancelProvider()
    runtime, transport, repo = setup(tmp_path, provider)
    runtime.prepare('thread', 'work', 'hello', durable=True)
    provider.on_start = lambda: repo.executions.request_cancellation('thread', 'work')
    assert (await runtime.advance_background('thread', 'work')).status == 'waiting_model'
    assert (await runtime.advance_background('thread', 'work')).status == 'cancelled'
    assert provider.posts == provider.cancels == 1
    await transport.close()


async def test_cancel_unknown_submission_never_reposts(tmp_path):
    provider = CancelProvider()
    runtime, transport, repo = setup(tmp_path, provider)
    runtime.prepare('thread', 'work', 'hello', durable=True)
    provider.error = httpx.ReadError('lost acknowledgement')
    with pytest.raises(httpx.ReadError):
        await runtime.advance_background('thread', 'work')
    repo.executions.request_cancellation('thread', 'work')
    result = await runtime.advance_background('thread', 'work')
    assert result.status == 'cancelled'
    assert result.events[-1].data['provider_outcome'] == 'submission_uncertain'
    assert provider.posts == 1 and provider.cancels == 0
    await transport.close()


async def test_cleanup_failure_and_lost_checkpoint_retry_only_idempotent_cancel(tmp_path, monkeypatch):
    provider = CancelProvider(outcomes=[503, 'cancelled'])
    runtime, transport, repo = setup(tmp_path, provider)
    runtime.prepare('thread', 'work', 'hello', durable=True)
    await runtime.advance_background('thread', 'work')
    repo.executions.request_cancellation('thread', 'work')
    with pytest.raises(httpx.HTTPStatusError):
        await runtime.advance_background('thread', 'work')
    original = repo.executions.save
    def fail_terminal(execution_id, worker_id, context, status):
        if status == 'cancelled':
            raise RuntimeError('lost checkpoint')
        original(execution_id, worker_id, context, status)
    monkeypatch.setattr(repo.executions, 'save', fail_terminal)
    with pytest.raises(RuntimeError):
        await runtime.advance_background('thread', 'work')
    await transport.close()
    replacement, transport, repo = setup(tmp_path, provider)
    assert (await replacement.advance_background('thread', 'work')).status == 'cancelled'
    assert provider.posts == 1 and provider.cancels == 3
    await transport.close()


async def test_cancel_recovers_committed_stock_receipt_without_reexecuting_tool(tmp_path, monkeypatch):
    provider = CancelProvider([{'status': 'completed', 'output': [stock_call(), stock_call(100)]}])
    runtime, transport, repo = setup(tmp_path, provider)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'quantity': 1, 'value': '10k'})
    runtime.prepare('thread', 'work', 'add two', durable=True)
    await runtime.advance_background('thread', 'work')
    await runtime.advance_background('thread', 'work')
    original = repo.executions.save
    def fail_result(execution_id, worker_id, context, status):
        if context.get('pending') and context['pending']['calls'][0].get('result'):
            raise RuntimeError('crash after inventory commit')
        original(execution_id, worker_id, context, status)
    monkeypatch.setattr(repo.executions, 'save', fail_result)
    with pytest.raises(RuntimeError):
        await runtime.advance_background('thread', 'work')
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 3
    repo.executions.request_cancellation('thread', 'work')
    await transport.close()
    replacement, transport, repo = setup(tmp_path, provider)
    result = await replacement.advance_background('thread', 'work')
    assert result.status == 'cancelled'
    assert replacement.registry.service.get(GetPartRequest(1)).quantity == 3
    assert len([e for e in result.events if e.kind == 'tool_result']) == 1
    assert provider.posts == 1 and provider.cancels == 0
    await transport.close()


async def test_approval_and_resume_cannot_reactivate_cancelled_work(tmp_path):
    update = {'type': 'function_call', 'name': 'update_part', 'call_id': 'update',
              'arguments': json.dumps({'part_id': 1, 'fields': {'description': 'changed'}})}
    provider = CancelProvider([{'status': 'completed', 'output': [update]}])
    runtime, transport, repo = setup(tmp_path, provider)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'quantity': 1, 'value': '10k'})
    runtime.prepare('thread', 'work', 'rename', durable=True)
    await runtime.advance_background('thread', 'work')
    await runtime.advance_background('thread', 'work')
    pending = await runtime.advance_background('thread', 'work')
    request_id = next(e.data['request_id'] for e in pending.events if e.kind == 'approval_request')
    repo.executions.request_cancellation('thread', 'work')
    with pytest.raises(ValueError, match='cancelled'):
        await runtime.advance_background('thread', 'work', approval_response=ApprovalResponse(request_id, True))
    with pytest.raises(ValueError, match='cancelled'):
        runtime.approvals.decide('thread', request_id, True)
    with pytest.raises(ValueError, match='irreversible'):
        repo.dispatch.wake('thread', 'work')
    assert (await runtime.advance_background('thread', 'work')).status == 'cancelled'
    assert repo.approvals.get('thread', request_id).decision is None
    assert runtime.registry.service.get(GetPartRequest(1)).description is None
    with pytest.raises(KeyError):
        repo.executions.request_cancellation('other-thread', 'work')
    await transport.close()


async def test_terminal_publication_gap_is_recovered_after_worker_replacement(tmp_path, monkeypatch):
    provider = CancelProvider()
    runtime, transport, repo = setup(tmp_path, provider)
    runtime.prepare('thread', 'work', 'hello', durable=True)
    repo.executions.request_cancellation('thread', 'work')
    original = runtime.store.append_once
    def fail_terminal(event, key):
        if event.kind == 'completed':
            raise RuntimeError('publication interrupted')
        return original(event, key)
    monkeypatch.setattr(runtime.store, 'append_once', fail_terminal)
    with pytest.raises(RuntimeError):
        await runtime.advance_background('thread', 'work')
    assert repo.executions.get('thread', 'work').status == 'cancelled'
    await transport.close()
    replacement, transport, repo = setup(tmp_path, provider)
    assert (await replacement.advance_background('thread', 'work')).status == 'cancelled'
    assert len([e for e in repo.conversations.events('thread') if e.kind == 'completed']) == 1
    assert not provider.requests
    await transport.close()

async def test_cancel_mid_tool_finishes_authorized_mutation_and_skips_next_call(tmp_path, monkeypatch):
    provider = CancelProvider([{'status': 'completed', 'output': [stock_call(), stock_call(100)]}])
    runtime, transport, repo = setup(tmp_path, provider)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'quantity': 1, 'value': '10k'})
    runtime.prepare('thread', 'work', 'add two', durable=True)
    await runtime.advance_background('thread', 'work')
    await runtime.advance_background('thread', 'work')
    original = runtime.registry.execute
    async def cancel_during_tool(*args, **kwargs):
        result = await original(*args, **kwargs)
        repo.executions.request_cancellation('thread', 'work')
        return result
    monkeypatch.setattr(runtime.registry, 'execute', cancel_during_tool)
    result = await runtime.advance_background('thread', 'work')
    assert result.status == 'cancelled'
    assert runtime.registry.service.get(GetPartRequest(1)).quantity == 3
    assert len([e for e in result.events if e.kind == 'tool_result']) == 1
    assert provider.posts == 1
    await transport.close()


async def test_dispatch_retries_cancel_cleanup_and_settles_without_lost_wake(tmp_path):
    from tests.agent_runtime.test_dispatch import build, due, row
    provider = CancelProvider(outcomes=[503, 'cancelled'])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'work', 'hello')
    assert await services.dispatch.run_once()
    services.dispatch.cancel(thread, 'work')
    assert await services.dispatch.run_once()
    assert row(services.domain.repository)['failures'] == 1
    await services.close()
    replacement = build(tmp_path, provider)
    due(replacement.domain.repository, expire=True)
    replacement.dispatch.reconcile()
    assert await replacement.dispatch.run_once()
    assert row(replacement.domain.repository)['settled'] == 1
    assert not await replacement.dispatch.run_once()
    assert [e.data['status'] async for e in replacement.dispatch.follow(thread, 'work') if e.kind == 'completed'] == ['cancelled']
    assert provider.posts == 1 and provider.cancels == 2
    await replacement.close()


async def test_http_cancel_is_scoped_idempotent_and_never_runs_provider(tmp_path):
    from starlette.testclient import TestClient
    from server import create_app
    from tests.agent_runtime.test_dispatch import build
    provider = CancelProvider()
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'work', 'hello')
    with TestClient(create_app(services)) as client:
        path = f'/agent/threads/{thread}/executions/work/cancel'
        assert client.post(path).json()['status'] == 'cancelling'
        assert client.post(path).json()['status'] == 'cancelling'
        assert client.post(f'/agent/threads/{thread}/executions/unknown/cancel').status_code == 404
        assert client.post('/agent/threads/other/executions/work/cancel').status_code == 404
        assert not provider.requests
        events = client.get(f'/agent/threads/{thread}/events').text
        assert 'cancellation_requested' in events
    # The TestClient closes services; a fresh worker owns the same durable data.
    services = build(tmp_path, provider)
    assert await services.dispatch.run_once()
    assert services.domain.repository.executions.get(thread, 'work').status == 'cancelled'
    assert not provider.requests
    await services.close()

async def test_blocked_cleanup_needs_explicit_cancel_retry_without_reviving_generation(tmp_path):
    from tests.agent_runtime.test_dispatch import build, due, row
    provider = CancelProvider(outcomes=[503])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'work', 'hello')
    await services.dispatch.run_once()
    services.dispatch.cancel(thread, 'work')
    for _ in range(8):
        due(services.domain.repository)
        assert await services.dispatch.run_once()
    assert row(services.domain.repository)['blocked'] == 1
    assert services.gateway.events(thread)[-2].data['message'].endswith('retry cancellation; do not resend it.')
    with pytest.raises(ValueError, match='irreversible'):
        services.dispatch.resume(thread, 'work')
    provider.outcomes = ['cancelled']
    services.dispatch.cancel(thread, 'work')
    assert await services.dispatch.run_once()
    assert services.domain.repository.executions.get(thread, 'work').status == 'cancelled'
    assert provider.posts == 1
    await services.close()


async def test_cancellation_survives_owned_stage_checkpoint_and_delivery_ack(tmp_path):
    from tests.agent_runtime.test_dispatch import build, row
    provider = CancelProvider()
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'work', 'hello')
    repo = services.domain.repository
    delivery = repo.dispatch.claim()
    record = repo.executions.claim(thread, 'work', 'worker')
    assert repo.executions.begin_stage('work', 'worker')
    services.dispatch.cancel(thread, 'work')
    repo.executions.save('work', 'worker', record.context, 'ready')
    assert not repo.executions.begin_stage('work', 'worker')
    repo.executions.release('work', 'worker')
    repo.dispatch.finish(delivery)
    assert row(repo)['settled'] == 0
    assert await services.dispatch.run_once()
    assert repo.executions.get(thread, 'work').status == 'cancelled'
    assert not provider.requests
    await services.close()
