"""Restart, retry, concurrency, and transaction failure checks for approvals."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from db.repository import SQLitePartsBinRepository

from agent_runtime import ApprovalEngine, ApprovalResponse, ModelTurn, OpenAIResponsesRuntime, ToolCall
from db.conversations import SQLiteConversationRepository
from db import persistence
from domain import AddPartRequest, GetPartRequest, PartFields, PartsBinService, UpdatePartRequest
from tools import PartsBinToolRegistry


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "parts.db"
    repository = SQLitePartsBinRepository(database)
    service = PartsBinService(repository)
    service.add_part(AddPartRequest(PartFields("resistor", "passive", 2, "10k", "0402")))
    engine = ApprovalEngine(repository)
    registry = PartsBinToolRegistry(service)
    return database, service, engine, registry


def request(engine, service, *, thread="thread", name="update_part", args=None):
    return engine.request(thread, name, args or {"part_id": 1, "fields": {"quantity": 10}}, service=service)


async def test_restart_before_decision_and_duplicate_after_later_edit(setup):
    database, service, engine, registry = setup
    pending = request(engine, service)
    restarted = ApprovalEngine(SQLitePartsBinRepository(database))
    approved = restarted.decide("thread", pending.request_id, True)
    first = await restarted.execute(approved, registry)
    assert first["result"]["quantity"] == 10
    service.update_part(UpdatePartRequest(1, {"quantity": 20}))
    duplicate = ApprovalEngine(SQLitePartsBinRepository(database)).decide("thread", pending.request_id, True)
    assert await restarted.execute(duplicate, registry) == first
    assert service.get(GetPartRequest(1)).quantity == 20


async def test_distinct_requests_and_thread_isolation(setup):
    _, service, engine, registry = setup
    one = request(engine, service)
    two = request(engine, service, thread="other")
    three = request(engine, service)
    assert len({one.request_id, two.request_id, three.request_id}) == 3
    with pytest.raises(ValueError, match="Unknown"):
        engine.decide("other", one.request_id, True)
    approved = engine.decide("other", two.request_id, True)
    assert (await engine.execute(approved, registry))["ok"]
    # The other proposals still exist, but their inventory snapshots are stale.
    assert not (await engine.execute(engine.decide("thread", one.request_id, True), registry))["ok"]


async def test_denial_is_final_and_cannot_execute(setup):
    database, service, engine, registry = setup
    pending = request(engine, service)
    denied = engine.decide("thread", pending.request_id, False)
    assert ApprovalEngine(SQLitePartsBinRepository(database)).decide("thread", pending.request_id, False) == denied
    with pytest.raises(ValueError, match="already"):
        engine.decide("thread", pending.request_id, True)
    with pytest.raises(ValueError, match="matching approval"):
        await engine.execute(denied, registry)
    assert service.get(GetPartRequest(1)).quantity == 2


async def test_changed_review_blocks_approval(setup):
    database, service, engine, registry = setup
    persistence.save_pending_review(database, 1, {"description": "first"}, [])
    pending = request(engine, service, name="apply_review", args={"part_id": 1})
    persistence.save_pending_review(database, 1, {"description": "replacement"}, [])
    approved = engine.decide("thread", pending.request_id, True)
    result = await engine.execute(approved, registry)
    assert result["error"]["code"] == "conflict"
    assert service.get(GetPartRequest(1)).description is None
    assert service.list_pending_reviews()[1]["fields"]["description"]["value"] == "replacement"


async def test_partial_bulk_failure_rolls_back_every_target(setup):
    _, service, engine, registry = setup
    service.add_part(AddPartRequest(PartFields("resistor", "passive", 3, "10k", "0603")))
    pending = request(engine, service, name="bulk_update_parts",
                      args={"part_ids": [1, 2], "fields": {"package": "0805"}})
    approved = engine.decide("thread", pending.request_id, True)
    result = await engine.execute(approved, registry)
    assert result["error"]["code"] == "conflict"
    assert [part.package for part in service.list()] == ["0402", "0603"]
    assert await engine.execute(approved, registry) == result


async def test_checkpoint_failure_rolls_back_inventory_and_can_retry(setup):
    _, service, engine, registry = setup
    pending = request(engine, service)
    approved = engine.decide("thread", pending.request_id, True)
    original = persistence.TransactionConnection.execute

    def fail_checkpoint(self, sql, *args):
        if sql.startswith("UPDATE agent_approvals SET result_json"):
            raise RuntimeError("worker failed before checkpoint")
        return original(self, sql, *args)

    with patch.object(persistence.TransactionConnection, "execute", fail_checkpoint):
        with pytest.raises(RuntimeError, match="checkpoint"):
            await engine.execute(approved, registry)
    assert service.get(GetPartRequest(1)).quantity == 2
    assert (await engine.execute(approved, registry))["result"]["quantity"] == 10


async def test_delete_replays_original_success_after_target_is_gone(setup):
    database, service, engine, registry = setup
    pending = request(engine, service, name="delete_part", args={"part_id": 1})
    approved = engine.decide("thread", pending.request_id, True)
    first = await engine.execute(approved, registry)
    assert first["result"] == {"part_id": 1, "deleted": True}
    assert await ApprovalEngine(SQLitePartsBinRepository(database)).execute(approved, registry) == first
    assert service.list() == []


def test_concurrent_workers_return_one_committed_result(setup):
    database, service, engine, _ = setup
    pending = request(engine, service, name="delete_part", args={"part_id": 1})
    approved = engine.decide("thread", pending.request_id, True)
    engines = [ApprovalEngine(SQLitePartsBinRepository(database)), ApprovalEngine(SQLitePartsBinRepository(database))]
    barrier = Barrier(2)

    def run(worker):
        registry = PartsBinToolRegistry(service)
        barrier.wait(timeout=5)
        return asyncio.run(worker.execute(approved, registry))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, engines))
    assert results[0] == results[1] == {"ok": True, "result": {"part_id": 1, "deleted": True}}
    assert service.list() == []


class Turns:
    def __init__(self, *turns):
        self.turns = iter(turns)

    async def complete(self, request):
        return next(self.turns)


async def test_runtime_recovers_commit_before_tool_result_event(setup, tmp_path):
    database, service, engine, registry = setup
    store = SQLiteConversationRepository(tmp_path / "events.db")
    runtime = OpenAIResponsesRuntime(Turns(ModelTurn(tool_calls=(
        ToolCall("delete_part", {"part_id": 1}, "delete"),))),
        registry=registry, store=store, approvals=engine)
    pending = await runtime.run("thread", "delete it")
    approval = next(event for event in pending.events if event.kind == "approval_request")
    decision = ApprovalResponse(approval.data["request_id"], True)

    def disconnected(event):
        if event.kind == "tool_result":
            raise RuntimeError("connection lost after commit")

    with pytest.raises(RuntimeError, match="connection lost"):
        await runtime.run("thread", "", approval_response=decision, on_event=disconnected)
    assert service.list() == []
    restarted = OpenAIResponsesRuntime(Turns(ModelTurn("Deleted.")), registry=registry,
        store=SQLiteConversationRepository(store.database), approvals=ApprovalEngine(SQLitePartsBinRepository(database)))
    result = await restarted.run("thread", "", approval_response=decision)
    assert result.status == "completed"
    saved = next(event for event in result.events if event.kind == "tool_result")
    assert saved.data["result"] == {"ok": True, "result": {"part_id": 1, "deleted": True}}


async def test_runtime_approval_survives_replacement_before_decision(setup, tmp_path):
    database, service, engine, registry = setup
    store = SQLiteConversationRepository(tmp_path / "events.db")
    runtime = OpenAIResponsesRuntime(Turns(ModelTurn(tool_calls=(
        ToolCall("update_part", {"part_id": 1, "fields": {"description": "new"}}, "update"),))),
        registry=registry, store=store, approvals=engine)
    pending = await runtime.run("thread", "update description")
    event = next(event for event in pending.events if event.kind == "approval_request")
    restarted = OpenAIResponsesRuntime(Turns(ModelTurn("Updated.")),
        registry=PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(database))),
        store=SQLiteConversationRepository(store.database), approvals=ApprovalEngine(SQLitePartsBinRepository(database)))
    result = await restarted.run("thread", "", approval_response=ApprovalResponse(event.data["request_id"], True))
    assert result.status == "completed"
    assert service.get(GetPartRequest(1)).description == "new"


async def test_review_write_provenance_and_removal_rollback_with_checkpoint(setup):
    database, service, engine, registry = setup
    provenance = [{"field_name": "description", "field_value": "evidenced",
                   "source_tier": "fixture", "source_kind": "test",
                   "extraction_method": "fixture", "evidence": "supporting passage"}]
    persistence.save_pending_review(database, 1, {"description": "evidenced"}, provenance)
    pending = request(engine, service, name="apply_review", args={"part_id": 1})
    approved = engine.decide("thread", pending.request_id, True)
    original = persistence.TransactionConnection.execute

    def fail_checkpoint(self, sql, *args):
        if sql.startswith("UPDATE agent_approvals SET result_json"):
            raise RuntimeError("checkpoint failure")
        return original(self, sql, *args)

    with patch.object(persistence.TransactionConnection, "execute", fail_checkpoint):
        with pytest.raises(RuntimeError):
            await engine.execute(approved, registry)
    assert service.get(GetPartRequest(1)).description is None
    assert service.list_pending_reviews()
    assert persistence.list_field_provenance(database, 1) == []
    result = await engine.execute(approved, registry)
    assert result["ok"]
    assert service.get(GetPartRequest(1)).description == "evidenced"
    assert service.list_pending_reviews() == {}
    assert persistence.list_field_provenance(database, 1)[0]["evidence"] == "supporting passage"


def test_approvals_require_inventory_database_and_gated_tool(setup, tmp_path):
    _, service, engine, registry = setup
    with pytest.raises(ValueError, match="does not require"):
        request(engine, service, name="add_stock", args={"part_id": 1, "quantity": 1})
    with pytest.raises(ValueError, match="transactional database"):
        ApprovalEngine(SQLitePartsBinRepository(tmp_path / "other.db")).request("thread", "delete_part", {"part_id": 1}, service=service)
