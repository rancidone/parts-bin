"""Search, approvals, evidence and recovery through the same typed services."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_runtime import ApprovalEngine, ConversationEvent
from domain import AddPartRequest, GetPartRequest, PartFields, PartsBinService, UpdatePartRequest
from domain.repositories import ExecutionBusy, ExecutionUnavailable, RepositoryBusy
from tools import PartsBinToolRegistry, ToolExecutionContext
from evaluation.inventory_scale import seed, workloads
from tests.storage import TestRepository


@pytest.fixture
def repository(tmp_path):
    return TestRepository(tmp_path / 'flows')


def service(repository):
    return PartsBinService(repository)


def part(repository, **changes):
    fields = {'part_category': 'resistor', 'profile': 'passive', 'quantity': 3,
              'value': '10k', 'package': '0402', **changes}
    return service(repository).add_part(AddPartRequest(PartFields(**fields)))


async def test_search_identity_units_candidates_and_qualified_specs(repository):
    seed(repository, 24)
    registry = PartsBinToolRegistry(service(repository))
    results = {}
    for name, tool, args in workloads(24, 3):
        response = await registry.execute(tool, args)
        assert response['ok'], response
        results[name] = response['result']
    assert results['exact_identity']['count'] == 1
    assert results['nominal_value']['count'] == 12
    assert results['narrow_candidates']['count'] == 1
    assert results['electrical_first_page']['match_count'] == 6
    assert results['electrical_first_page']['incomplete_count'] == 12
    assert results['electrical_first_page']['next_offset'] == 3
    assert results['electrical_later_page']['next_offset'] == 6
    assert results['electrical_first_page']['matches'] != results['electrical_later_page']['matches']
    assert results['overlapping_bom']['overlapping_requirements']


async def test_atomic_effect_receipt_and_duplicate_stock(repository):
    target = part(repository)
    registry = PartsBinToolRegistry(service(repository))
    context = ToolExecutionContext(operation_id='stable-stock')
    arguments = {'part_id': target.id, 'quantity': 2}
    first = await registry.execute('add_stock', arguments, context=context)
    assert first['ok'] and first['result']['quantity'] == 5
    assert await registry.execute('add_stock', arguments, context=context) == first
    assert repository.inventory.get(target.id).quantity == 5
    mismatch = await registry.execute('add_stock', {**arguments, 'quantity': 10}, context=context)
    assert mismatch['error']['code'] == 'conflict'


async def test_single_approval_replay_and_stale_target(repository):
    target = part(repository)
    domain = service(repository)
    engine = ApprovalEngine(repository)
    request = engine.request('thread', 'update_part', {'part_id': target.id, 'fields': {'quantity': 7}}, service=domain)
    approved = engine.decide('thread', request.request_id, True)
    first = await engine.execute(approved, PartsBinToolRegistry(domain))
    assert first['result']['quantity'] == 7
    domain.update_part(UpdatePartRequest(target.id, {'quantity': 9}))
    assert await engine.execute(approved, PartsBinToolRegistry(domain)) == first
    stale = engine.request('thread', 'delete_part', {'part_id': target.id}, service=domain)
    domain.update_part(UpdatePartRequest(target.id, {'quantity': 10}))
    result = await engine.execute(engine.decide('thread', stale.request_id, True), PartsBinToolRegistry(domain))
    assert result['error']['code'] == 'conflict'
    assert repository.inventory.get(target.id).quantity == 10


async def test_partial_bulk_resumes_after_worker_dies(repository, monkeypatch):
    first, second = part(repository), part(repository, package='0603')
    domain = service(repository)
    engine = ApprovalEngine(repository)
    request = engine.request('thread', 'bulk_update_parts',
        {'part_ids': [first.id, second.id], 'fields': {'package': '0805'}}, service=domain)
    approved = engine.decide('thread', request.request_id, True)
    original = engine._execute_one

    async def interrupt(request, *args):
        if request.arguments['part_id'] == second.id:
            raise RuntimeError('worker died between parts')
        return await original(request, *args)

    with monkeypatch.context() as patch:
        patch.setattr(engine, '_execute_one', interrupt)
        with pytest.raises(RuntimeError, match='between parts'):
            await engine.execute(approved, PartsBinToolRegistry(domain))
    assert repository.inventory.get(first.id).package == '0805'
    # A later edit must survive replay of the already committed first receipt.
    domain.update_part(UpdatePartRequest(first.id, {'description': 'later edit'}))
    result = await ApprovalEngine(repository).execute(approved, PartsBinToolRegistry(domain))
    assert result['ok']
    assert [row['id'] for row in result['result']['parts']] == [first.id]
    assert result['result']['errors'][0]['part_id'] == second.id
    assert repository.inventory.get(first.id).description == 'later edit'
    assert await engine.execute(approved, PartsBinToolRegistry(domain)) == result


def test_failed_transaction_preserves_no_effect_or_receipt(repository):
    from domain.repositories import StoredOperation
    with pytest.raises(RuntimeError, match='crash'):
        with repository.transaction() as unit:
            unit.inventory.insert(vars(PartFields('resistor', 'passive', 3, '10k', '0402')))
            unit.operations.insert(StoredOperation('id', 'add_part', {}, {'id': 1}))
            raise RuntimeError('crash before commit')
    assert repository.inventory.search({}) == []
    assert repository.operations.get('id') is None


async def test_rejected_mutation_rolls_back_then_saves_failure(repository, monkeypatch):
    target = part(repository)
    domain = service(repository)
    engine = ApprovalEngine(repository)
    request = engine.request('thread', 'update_part', {'part_id': target.id, 'fields': {'quantity': 7}}, service=domain)
    approved = engine.decide('thread', request.request_id, True)

    async def partial(self, name, args, **kwargs):
        self.service.repository.inventory.increment_stock(target.id, 100)
        return {'ok': False, 'error': {'code': 'invalid_input', 'message': 'rejected', 'details': {}}}

    with monkeypatch.context() as patch:
        patch.setattr(PartsBinToolRegistry, 'execute', partial)
        result = await engine.execute(approved, PartsBinToolRegistry(domain))
    assert repository.inventory.get(target.id).quantity == 3
    assert repository.approvals.get('thread', request.request_id).result == result
    assert await engine.execute(approved, PartsBinToolRegistry(domain)) == result


async def test_review_provenance_and_delete(repository):
    target = part(repository)
    domain = service(repository)
    evidence = [{'field_name': 'description', 'field_value': 'verified', 'source_tier': 'fixture',
                 'source_kind': 'test', 'extraction_method': 'fixture', 'evidence': 'supporting passage'}]
    repository.inventory.save_pending_review(target.id, {'description': 'verified'}, evidence)
    engine = ApprovalEngine(repository)
    review = engine.request('thread', 'apply_review', {'part_id': target.id}, service=domain)
    result = await engine.execute(engine.decide('thread', review.request_id, True), PartsBinToolRegistry(domain))
    assert result['ok']
    assert repository.inventory.get(target.id).description == 'verified'
    assert repository.inventory.list_provenance(target.id)[0]['evidence'] == 'supporting passage'
    assert repository.inventory.list_pending_reviews() == {}
    repository.inventory.delete(target.id)
    assert repository.inventory.list_provenance(target.id) == []


def test_lease_large_checkpoint_and_input_identity(repository):
    executions = repository.executions
    context = {'input_digest': 'first', 'reasoning': 'x' * 1_000_000}
    executions.prepare('thread', 'execution', context)
    claimed = executions.claim('thread', 'execution', 'worker')
    assert claimed.context == context
    with pytest.raises(ExecutionBusy):
        executions.claim('thread', 'execution', 'other')
    with pytest.raises(ExecutionUnavailable):
        executions.prepare('thread', 'execution', {'input_digest': 'changed'})
    executions.save('execution', 'worker', {**context, 'approval_id': 'approval'}, 'awaiting_approval')
    assert executions.for_approval('thread', 'approval').execution_id == 'execution'
    executions.release('execution', 'worker')
    with pytest.raises(ExecutionUnavailable):
        executions.assert_owned('execution', 'worker')
    assert executions.claim('thread', 'execution', 'replacement').status == 'awaiting_approval'


def test_conversation_events_and_order(repository):
    conversations = repository.conversations
    event = ConversationEvent('user_message', 'thread', 'openai', {'text': 'Find my parts'})
    saved = conversations.append_once(event, 'stable-event')
    assert conversations.append_once(event, 'stable-event') == saved
    conversations.append(ConversationEvent('assistant_text', 'thread', 'openai', {'text': 'Found them'}))
    assert [event.sequence for event in conversations.events('thread')] == [1, 2]
    assert conversations.threads()[0].title == 'Find my parts'
    with pytest.raises(ValueError):
        conversations.append_once(ConversationEvent('user_message', 'other', 'openai', {}), 'stable-event')


def test_concurrent_approvals_apply_once(repository):
    target = part(repository)
    domain = service(repository)
    engine = ApprovalEngine(repository)
    request = engine.request('thread', 'delete_part', {'part_id': target.id}, service=domain)
    approved = engine.decide('thread', request.request_id, True)
    barrier = Barrier(2)

    def run(_):
        barrier.wait(timeout=10)
        return asyncio.run(engine.execute(approved, PartsBinToolRegistry(domain)))

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(run, range(2)))
    assert results[0] == results[1]
    assert repository.inventory.get(target.id) is None


def test_concurrent_event_delivery_and_intake(repository):
    event = ConversationEvent('user_message', 'thread', 'openai', {'text': 'hello'})
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: repository.conversations.append_once(event, 'event'), range(2)))
    assert results[0] == results[1]
    assert len(repository.conversations.events('thread')) == 1
    context = {'input_digest': 'same'}
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: repository.executions.prepare('thread', 'execution', context), range(2)))
    assert results[0] == results[1]


def test_concurrent_bulk_create_adds_one_identity_without_incrementing(repository):
    from domain import CreatePartsRequest
    request = CreatePartsRequest((PartFields('resistor', 'passive', 20, '2r2', '0603'),))
    barrier = Barrier(2)
    def create():
        barrier.wait(timeout=5)
        return service(repository).create_parts(request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sorted(result[0].status for result in results) == ['added', 'skipped']
    assert len(service(repository).list()) == 1
    assert service(repository).list()[0].quantity == 20
