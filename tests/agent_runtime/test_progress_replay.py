"""Cursor observation cannot claim, schedule or repeat durable stages."""

import json
import httpx

from agent_runtime import ApprovalResponse
from tests.agent_runtime.test_background_workers import Provider
from tests.agent_runtime.test_dispatch import build, due


async def test_cursor_replay_survives_disconnect_without_worker_or_provider_side_effects(tmp_path):
    from server import create_app
    provider = Provider([{'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': 'Saved answer'}]}]}])
    services = build(tmp_path, provider)
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'work', 'hello')
    repository = services.domain.repository
    with repository.unit(write=False) as unit:
        before = dict(unit.connection.execute('SELECT * FROM pb_revision').fetchone())
        lease = dict(unit.connection.execute('SELECT * FROM agent_dispatch').fetchone())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url='http://test') as client:
        url = f'/agent/threads/{thread}/events'
        replay = await client.get(url, params={'after': 0, 'execution_id': 'work'})
        assert replay.headers['x-agent-active'] == replay.headers['x-execution-accepted'] == 'true'
        assert 'user_message' in replay.text
        for _ in range(3):
            empty = await client.get(url, params={'after': 1, 'execution_id': 'work'})
            assert empty.text == ''
            assert empty.headers['x-agent-active'] == 'true'
        unknown = await client.get(url, params={'after': 1, 'execution_id': 'missing'})
        assert unknown.headers['x-execution-accepted'] == 'false'
        assert not provider.requests
        with repository.unit(write=False) as unit:
            assert dict(unit.connection.execute('SELECT * FROM pb_revision').fetchone()) == before
            assert dict(unit.connection.execute('SELECT * FROM agent_dispatch').fetchone()) == lease
        # Worker operates independently after the original observer disappeared.
        await services.dispatch.run_once()
        due(repository)
        await services.dispatch.run_once()
        resumed = await client.get(url, params={'after': 1})
        assert resumed.headers['x-agent-active'] == 'false'
        assert 'Saved answer' in resumed.text and 'completed' in resumed.text
        assert 'user_message' not in resumed.text
        assert provider.posts == 1
        events = [json.loads(line[6:]) for line in resumed.text.splitlines() if line.startswith('data: ')]
        assert [event['sequence'] for event in events] == [2, 3]
        assert (await client.get(url, params={'after': 3})).text == ''
    await services.close()


async def test_approval_and_blocked_waits_are_not_mistaken_for_running_work(tmp_path):
    update = {'type': 'function_call', 'name': 'update_part', 'call_id': 'update',
              'arguments': json.dumps({'part_id': 1, 'fields': {'description': 'new'}})}
    services = build(tmp_path, Provider([{'status': 'completed', 'output': [update]}]))
    thread = services.gateway.create_thread()
    runtime = await services.gateway._runtime_for(thread)
    await runtime.registry.execute('add_part', {'part_category': 'resistor', 'profile': 'passive', 'value': '10k', 'quantity': 1})
    await services.dispatch.submit(thread, 'work', 'rename')
    for _ in range(3):
        due(services.domain.repository)
        await services.dispatch.run_once()
    repository = services.domain.repository
    assert not repository.dispatch.progress_state(thread).active
    request_id = next(event.data['request_id'] for event in services.gateway.events(thread) if event.kind == 'approval_request')
    services.dispatch.decide(thread, ApprovalResponse(request_id, True))
    assert repository.dispatch.progress_state(thread).active
    with repository.transaction() as unit:
        unit.connection.execute('UPDATE agent_dispatch SET blocked = 1')
    assert not repository.dispatch.progress_state(thread).active
    await services.close()


async def test_terminal_publication_gap_remains_observable_and_unknown_threads_are_not_created(tmp_path):
    from server import create_app
    services = build(tmp_path, Provider([]))
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'work', 'hello')
    repository = services.domain.repository
    record = repository.executions.claim(thread, 'work', 'worker')
    repository.executions.save('work', 'worker', record.context, 'completed')
    repository.executions.release('work', 'worker')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url='http://test') as client:
        gap = await client.get(f'/agent/threads/{thread}/events', params={'after': 1})
        assert gap.text == '' and gap.headers['x-agent-active'] == 'true'
        missing = await client.get('/agent/threads/missing/events', params={'after': 0})
        assert missing.status_code == 404
        assert repository.conversations.runtime_for('missing') is None
    await services.close()


async def test_progress_state_and_events_share_a_snapshot_during_worker_completion(tmp_path, monkeypatch):
    from agent_runtime import ConversationEvent
    from db.postgres.dispatch import PostgresDispatchRepository
    services = build(tmp_path, Provider([]))
    thread = services.gateway.create_thread()
    await services.dispatch.submit(thread, 'work', 'hello')
    repository = services.domain.repository
    delivery = repository.dispatch.claim()
    record = repository.executions.claim(thread, 'work', 'worker')
    original = PostgresDispatchRepository.progress_state
    injected = False

    def finish_between_reads(self, thread_id, execution_id=None):
        nonlocal injected
        state = original(self, thread_id, execution_id)
        if not injected:
            injected = True
            with repository.transaction() as unit:
                unit.executions.save('work', 'worker', record.context, 'completed')
                unit.conversations.append_once(ConversationEvent('assistant_text', thread, 'openai',
                    {'text': 'Finished', 'execution_id': 'work'}), 'work:text')
                unit.conversations.append_once(ConversationEvent('completed', thread, 'openai',
                    {'status': 'completed', 'execution_id': 'work'}), 'work:terminal')
                unit.dispatch.finish(delivery)
        return state

    monkeypatch.setattr(PostgresDispatchRepository, 'progress_state', finish_between_reads)
    snapshot = repository.dispatch.progress_snapshot(thread, after=1)
    assert snapshot.state.active and snapshot.events == ()
    completed = repository.dispatch.progress_snapshot(thread, after=1)
    assert not completed.state.active
    assert [event.sequence for event in completed.events] == [2, 3]
    await services.close()


def test_http_host_rejects_mismatched_conversation_and_execution_databases(tmp_path):
    import pytest
    from application import create_services
    from server import create_app
    from tests.storage import TestRepository
    inventory = TestRepository(tmp_path / 'inventory')
    history = TestRepository(tmp_path / 'history').conversations
    services = create_services(inventory, history, transport_factory=lambda: None)
    with pytest.raises(ValueError, match='share one transactional database'):
        create_app(services)
