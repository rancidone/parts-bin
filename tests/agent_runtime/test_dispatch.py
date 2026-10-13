"""Deliver stages across crashes, duplicate callbacks and replacement workers."""

import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from starlette.testclient import TestClient

from agent_runtime import ApprovalResponse, OpenAIResponsesTransport
from application import create_services
from db.postgres.connection import Adapter
from domain.repositories import ExecutionBusy
from server import create_app
from tests.storage import TestRepository
from tests.agent_runtime.test_background_workers import Provider, stock_call

SECRET_BYTES = b'test-signing-secret-not-a-production-key'
SECRET = 'whsec_' + base64.b64encode(SECRET_BYTES).decode()


def build(path, provider):
    repository = TestRepository(path)
    transport = OpenAIResponsesTransport(api_key='test-only', model='test-model',
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)))
    return create_services(repository, repository.conversations, transport_factory=lambda: transport)


def due(repository, *, expire=False):
    with repository.transaction() as unit:
        unit.connection.execute('UPDATE agent_dispatch SET available_at = 0' +
                                (', lease_until = 0' if expire else ''))
        if expire:
            unit.connection.execute('UPDATE agent_executions SET lease_until = 0')


def row(repository):
    with repository.unit(write=False) as unit:
        return unit.connection.execute('SELECT * FROM agent_dispatch').fetchone()


async def test_retry_missed_webhook_and_replacement_workers_mutate_once(tmp_path):
    provider = Provider([503, {'status': 'completed', 'output': [stock_call()]},
                         {'status': 'completed', 'output': []}])
    services = build(tmp_path, provider)
    await (await services.gateway._runtime_for(services.gateway.create_thread())).registry.execute('add_part',
        {'part_category': 'resistor', 'profile': 'passive', 'value': '10k', 'quantity': 1})
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'Add two')
    await services.dispatch.submit(thread, 'request', 'Add two')
    assert await services.dispatch.run_once()
    assert row(services.domain.repository)['available_at'] > time.time()
    await services.close()
    for expected_status, expected_quantity in [('waiting_model', 1), ('ready', 1), ('ready', 3),
                                               ('waiting_model', 3), ('completed', 3)]:
        services = build(tmp_path, provider)
        services.domain.repository.dispatch.reconcile()
        due(services.domain.repository)
        assert await services.dispatch.run_once()
        record = services.domain.repository.executions.get(thread, 'request')
        assert record.status == expected_status
        assert services.domain.list()[0].quantity == expected_quantity
        await services.close()
    assert provider.posts == 2
    services = build(tmp_path, provider)
    assert not await services.dispatch.run_once()
    services.dispatch.resume(thread, 'request')
    assert await services.dispatch.run_once()
    assert provider.posts == 2
    assert len([event for event in services.gateway.events(thread) if event.kind == 'tool_result']) == 1
    assert row(services.domain.repository)['available_at'] is None
    await services.close()


async def test_early_webhook_is_atomic_deduplicated_and_wakes_saved_id(tmp_path):
    provider = Provider([{'status': 'completed', 'output': []}])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'Hello')
    inbox = services.domain.repository.dispatch
    assert inbox.notify('event', 'resp_1', 'response.completed')
    assert not inbox.notify('event', 'resp_1', 'response.completed')
    assert await services.dispatch.run_once()
    assert row(services.domain.repository)['available_at'] <= time.time()
    assert await services.dispatch.run_once()
    # Late duplicate after completion cannot initiate another model stage.
    assert inbox.notify('late-event', 'resp_1', 'response.completed')
    assert not await services.dispatch.run_once()
    assert provider.posts == 1
    await services.close()


async def test_wakeup_during_owned_delivery_survives_ack_and_stale_ack_is_fenced(tmp_path):
    services = build(tmp_path, Provider([]))
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'Hello')
    repository = services.domain.repository
    delivery = repository.dispatch.claim()
    assert repository.dispatch.claim() is None
    await services.gateway.advance_background(thread, 'request')
    repository.dispatch.notify('event', 'resp_1', 'response.completed')
    repository.dispatch.finish(delivery)
    assert row(repository)['available_at'] <= time.time()
    replacement = repository.dispatch.claim()
    repository.dispatch.finish(delivery, failure='stale')
    assert row(repository)['token'] == replacement.token
    due(repository, expire=True)
    third = repository.dispatch.claim()
    repository.dispatch.finish(replacement)
    assert row(repository)['token'] == third.token
    await services.close()


async def test_lost_submission_ack_is_failed_without_another_paid_post(tmp_path):
    provider = Provider([])
    provider.error = httpx.ReadTimeout('contains private request')
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'Hello')
    assert await services.dispatch.run_once()
    assert row(services.domain.repository)['last_error'] == 'ReadTimeout'
    await services.close()
    provider.error = None
    replacement = build(tmp_path, provider)
    due(replacement.domain.repository, expire=True)
    assert await replacement.dispatch.run_once()
    assert replacement.gateway.events(thread)[-2].data['code'] == 'model_submission_uncertain'
    assert provider.posts == 1
    await replacement.close()


async def test_checkpoint_and_next_delivery_roll_back_together(tmp_path, monkeypatch):
    services = build(tmp_path, Provider([]))
    thread = services.gateway.create_thread()
    repository = services.domain.repository
    original = Adapter.execute

    def broken(self, sql, parameters=()):
        if sql.startswith('INSERT INTO agent_dispatch'):
            raise RuntimeError('outbox unavailable')
        return original(self, sql, parameters)

    monkeypatch.setattr(Adapter, 'execute', broken)
    with pytest.raises(RuntimeError):
        await services.dispatch.submit(thread, 'request', 'Hello')
    assert repository.executions.get(thread, 'request') is None
    monkeypatch.setattr(Adapter, 'execute', original)
    await services.dispatch.submit(thread, 'request', 'Hello')
    delivery = repository.dispatch.claim()
    repository.executions.claim(thread, 'request', 'runtime')
    initial = repository.executions.get(thread, 'request').context

    def fail_schedule(self, sql, parameters=()):
        if sql.startswith('UPDATE agent_dispatch SET response_id'):
            raise RuntimeError('schedule unavailable')
        return original(self, sql, parameters)

    monkeypatch.setattr(Adapter, 'execute', fail_schedule)
    with pytest.raises(RuntimeError):
        repository.executions.save('request', 'runtime', {**initial, 'model_response_id': 'resp_test'}, 'waiting_model')
    assert repository.executions.get(thread, 'request').context == initial
    assert row(repository)['token'] == delivery.token
    await services.close()


async def test_reconcile_does_not_adopt_historical_or_approval_waits(tmp_path):
    update = {'type': 'function_call', 'name': 'update_part', 'call_id': 'update',
              'arguments': json.dumps({'part_id': 1, 'fields': {'description': 'new'}})}
    provider = Provider([{'status': 'completed', 'output': [update]}, {'status': 'completed', 'output': []}])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    runtime = await services.gateway._runtime_for(thread)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'value': '10k', 'quantity': 1})
    await services.dispatch.submit(thread, 'request', 'Rename')
    for _ in range(3):
        due(services.domain.repository)
        await services.dispatch.run_once()
    approval = next(event.data['request_id'] for event in services.gateway.events(thread) if event.kind == 'approval_request')
    assert row(services.domain.repository)['available_at'] is None
    other = services.gateway.create_thread()
    await services.gateway.prepare(other, 'historical', 'Never run this automatically')
    services.domain.repository.dispatch.reconcile()
    assert not await services.dispatch.run_once()
    services.dispatch.decide(thread, ApprovalResponse(approval, True))
    services.dispatch.decide(thread, ApprovalResponse(approval, True))
    for _ in range(3):
        due(services.domain.repository)
        await services.dispatch.run_once()
    assert services.domain.list()[0].description == 'new'
    assert provider.posts == 2
    assert services.domain.repository.executions.get(other, 'historical').status == 'ready'
    await services.close()


async def test_terminal_checkpoint_without_publication_recovers_after_lease_expiry(tmp_path, monkeypatch):
    services = build(tmp_path, Provider([{'status': 'completed', 'output': []}]))
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'Hello')
    await services.dispatch.run_once()
    delivery = services.domain.repository.dispatch.claim()  # Waiting rows aren't due yet.
    assert delivery is None
    due(services.domain.repository)
    delivery = services.domain.repository.dispatch.claim()
    runtime = await services.gateway._runtime_for(thread)
    original = runtime.store.append_once

    def crash(event, key):
        if event.kind == 'completed':
            raise RuntimeError('publication interrupted')
        return original(event, key)

    monkeypatch.setattr(runtime.store, 'append_once', crash)
    with pytest.raises(RuntimeError):
        await services.gateway.advance_background(thread, 'request')
    assert services.domain.repository.executions.get(thread, 'request').status == 'completed'
    monkeypatch.setattr(runtime.store, 'append_once', original)
    due(services.domain.repository, expire=True)
    services.domain.repository.dispatch.reconcile()
    await services.dispatch.run_once()
    assert services.gateway.events(thread)[-1].data['status'] == 'completed'
    assert row(services.domain.repository)['available_at'] is None
    await services.close()


def signed(payload, *, timestamp=None, event_id='evt_test'):
    raw = json.dumps(payload).encode()
    timestamp = str(timestamp or int(time.time()))
    signature = base64.b64encode(hmac.new(SECRET_BYTES,
        f'{event_id}.{timestamp}.'.encode() + raw, hashlib.sha256).digest()).decode()
    return raw, {'webhook-id': event_id, 'webhook-timestamp': timestamp, 'webhook-signature': 'v1,' + signature}


def test_webhook_verification_intake_and_storage_failure(tmp_path, monkeypatch):
    services = build(tmp_path, Provider([]))
    with TestClient(create_app(services, webhook_secret=SECRET)) as client:
        payload = {'id': 'evt_test', 'object': 'event', 'created_at': int(time.time()),
                   'type': 'response.completed', 'data': {'id': 'resp_test'}}
        raw, headers = signed(payload)
        assert client.post('/agent/webhooks/openai', content=raw, headers=headers).status_code == 204
        assert client.post('/agent/webhooks/openai', content=raw, headers=headers).status_code == 204
        assert client.post('/agent/webhooks/openai', content=raw + b' ', headers=headers).status_code == 400
        old, old_headers = signed(payload, timestamp=int(time.time()) - 600)
        assert client.post('/agent/webhooks/openai', content=old, headers=old_headers).status_code == 400
        assert client.post('/agent/webhooks/openai', content=b'x' * 65537, headers=headers).status_code == 413
        assert client.post('/agent/webhooks/openai', content=raw).status_code == 400
        with services.domain.repository.unit(write=False) as unit:
            assert unit.connection.execute('SELECT COUNT(*) AS n FROM agent_webhook_inbox').fetchone()['n'] == 1
        from db.postgres.dispatch import PostgresDispatchRepository
        monkeypatch.setattr(PostgresDispatchRepository, 'notify', lambda *args: (_ for _ in ()).throw(RuntimeError('database down')))
        assert client.post('/agent/webhooks/openai', content=raw, headers=headers).status_code == 503


async def test_reconciliation_repairs_missing_due_and_exhausted_retry_publication(tmp_path, monkeypatch):
    provider = Provider([])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'Hello')
    with services.domain.repository.transaction() as unit:
        unit.connection.execute('UPDATE agent_dispatch SET available_at = NULL')
    assert not await services.dispatch.run_once()
    assert services.dispatch.reconcile() == 1
    assert await services.dispatch.run_once()
    provider.error = httpx.ConnectError('private error')
    for _ in range(8):
        due(services.domain.repository)
        assert await services.dispatch.run_once()
    assert row(services.domain.repository)['blocked'] == 1
    assert row(services.domain.repository)['last_error'] == 'ConnectError'
    assert services.gateway.events(thread)[-1].data['code'] == 'dispatch_blocked'
    before = services.gateway.events(thread)
    services.dispatch.reconcile()
    assert services.gateway.events(thread) == before
    assert not await services.dispatch.run_once()
    provider.error = None
    provider.responses.append({'status': 'completed', 'output': []})
    services.dispatch.resume(thread, 'request')
    assert await services.dispatch.run_once()
    assert provider.posts == 1
    assert services.gateway.events(thread)[-1].data['status'] == 'completed'
    await services.close()


async def test_photo_handoff_never_persists_image_and_recovery_does_not_resubmit(tmp_path):
    from agent_runtime import ImageInput
    provider = Provider([{'status': 'completed', 'output': []}])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'photo-request', 'Identify', ImageInput('image/png', 'PRIVATE_IMAGE_BYTES'))
    assert provider.posts == 1
    assert services.domain.repository.executions.get(thread, 'photo-request').status == 'waiting_model'
    assert 'PRIVATE_IMAGE_BYTES' not in repr(services.domain.repository.executions.get(thread, 'photo-request'))
    assert 'PRIVATE_IMAGE_BYTES' not in repr(row(services.domain.repository))
    await services.close()
    replacement = build(tmp_path, provider)
    due(replacement.domain.repository)
    await replacement.dispatch.run_once()
    assert provider.posts == 1
    await replacement.close()


async def test_concurrent_delivery_claim_has_one_owner(tmp_path):
    import asyncio
    services = build(tmp_path, Provider([]))
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'request', 'Hello')
    dispatch = services.domain.repository.dispatch
    results = await asyncio.gather(asyncio.to_thread(dispatch.claim), asyncio.to_thread(dispatch.claim))
    assert sum(item is not None for item in results) == 1
    await services.close()


async def test_approval_decision_rolls_back_when_wakeup_fails(tmp_path, monkeypatch):
    update = {'type': 'function_call', 'name': 'update_part', 'call_id': 'update',
              'arguments': json.dumps({'part_id': 1, 'fields': {'description': 'new'}})}
    services = build(tmp_path, Provider([{'status': 'completed', 'output': [update]}]))
    thread = services.gateway.create_thread()
    runtime = await services.gateway._runtime_for(thread)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'value': '10k', 'quantity': 1})
    await services.dispatch.submit(thread, 'request', 'Rename')
    for _ in range(3):
        due(services.domain.repository)
        await services.dispatch.run_once()
    request_id = next(event.data['request_id'] for event in services.gateway.events(thread) if event.kind == 'approval_request')
    original = Adapter.execute
    def broken(self, sql, parameters=()):
        if sql.startswith('UPDATE agent_dispatch SET available_at'):
            raise RuntimeError('outbox failure')
        return original(self, sql, parameters)
    monkeypatch.setattr(Adapter, 'execute', broken)
    with pytest.raises(RuntimeError):
        services.dispatch.decide(thread, ApprovalResponse(request_id, True))
    assert services.domain.repository.approvals.get(thread, request_id).decision is None
    assert row(services.domain.repository)['available_at'] is None
    await services.close()
