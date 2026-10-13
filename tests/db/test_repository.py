"""Storage contract checks for transactions shared by inventory and approvals."""

import pytest

from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, GetPartRequest, PartFields, PartsBinService
from domain.repositories import StoredApproval


def fields():
    return vars(PartFields("resistor", "passive", 2, "10k", "0402"))


def approval():
    return StoredApproval("operation", "thread", "update_part", {"part_id": 1}, {})


def test_inventory_and_approval_share_commit_and_reopen(tmp_path):
    database = tmp_path / "parts.db"
    repository = SQLitePartsBinRepository(database)
    with repository.transaction() as unit:
        unit.inventory.insert(fields())
        unit.approvals.insert(approval())
        unit.approvals.set_decision("operation", True)
        unit.approvals.save_result("operation", {"ok": True})
    reopened = SQLitePartsBinRepository(database)
    assert reopened.inventory.get(1).quantity == 2
    record = reopened.approvals.get("thread", "operation")
    assert record.decision is True and record.result == {"ok": True}
    assert reopened.approvals.get("other-thread", "operation") is None


def test_transaction_failure_rolls_back_inventory_and_approval(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    with pytest.raises(RuntimeError):
        with repository.transaction() as unit:
            unit.inventory.insert(fields())
            unit.approvals.insert(approval())
            raise RuntimeError("worker lost")
    assert repository.inventory.search({}) == []
    assert repository.approvals.get("thread", "operation") is None


def test_savepoint_reverts_mutation_while_retaining_failure_outcome(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    with repository.transaction() as unit:
        unit.approvals.insert(approval())
        with unit.savepoint() as point:
            unit.inventory.insert(fields())
            point.rollback()
        unit.approvals.save_result("operation", {"ok": False})
    assert repository.inventory.search({}) == []
    assert repository.approvals.get("thread", "operation").result == {"ok": False}


def test_enrichment_can_stage_inside_unit_of_work(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    service = PartsBinService(repository)
    original = service.add_part(AddPartRequest(PartFields("transistor", "discrete_ic", 2, part_number="PBSS5350T")))
    with service.transaction() as (bound, _unit):
        bound.stage_enrichment(original, {"description": "PNP transistor"}, [
            {"field_name": "description", "field_value": "PNP transistor", "evidence": "datasheet passage"}])
    assert service.get(GetPartRequest(original.id)).description is None
    assert service.list_pending_reviews()[original.id]["fields"]["description"]["value"] == "PNP transistor"


def test_storage_preserves_supplied_representation_without_normalizing(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    values = {**fields(), 'value': '22K'}
    part_id = repository.inventory.insert(values)
    assert repository.inventory.get(part_id).value == '22K'
    assert repository.inventory.search({'part_category': 'resistor', 'value': '22k'}) == []
    assert repository.inventory.search({'part_category': 'resistor', 'value': '22K'})[0].id == part_id


def test_batch_specifications_use_one_connection_and_bounded_queries(tmp_path, monkeypatch):
    import sqlite3
    from db import persistence
    from evaluation.inventory_scale import seed

    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    seed(repository, 503)
    expected = {part_id: repository.inventory.specifications(part_id) for part_id in (1, 500, 501, 503)}
    original_connect = persistence._connect
    connections, statements = [], []

    def traced_connect(database):
        connection = original_connect(database)
        connection.set_trace_callback(statements.append)
        connections.append(connection)
        return connection

    monkeypatch.setattr(persistence, '_connect', traced_connect)
    assert repository.inventory.specifications_for_parts([]) == {}
    assert connections == []
    found = repository.inventory.specifications_for_parts([*range(1, 504), 1, 99999])
    assert len(connections) == 1
    assert sum(statement.startswith('SELECT part_id, facts_json') for statement in statements) == 2
    assert 'BEGIN' in statements and 'COMMIT' in statements
    assert 99999 not in found
    # Unaccepted proposals and absent facts never enter the accepted map.
    assert 2 not in found and 3 not in found
    assert {part_id: found.get(part_id, []) for part_id in expected} == expected
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connections[0].execute('SELECT 1')


def test_batch_specifications_use_existing_transaction_and_preserve_rollback(tmp_path):
    from evaluation.inventory_scale import seed

    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    seed(repository, 4)
    with pytest.raises(RuntimeError, match='roll back'):
        with repository.transaction() as unit:
            # Batch reads see the caller's in-progress writes without committing them.
            unit.inventory.apply_specification_review(3)
            assert set(unit.inventory.specifications_for_parts([1, 2, 3, 4])) == {1, 3, 4}
            raise RuntimeError('roll back')
    assert set(repository.inventory.specifications_for_parts([1, 2, 3, 4])) == {1, 4}
    assert 3 in repository.inventory.specification_reviews()


def test_legacy_identity_index_migrates_on_copy_without_changing_authoritative_data(tmp_path):
    import sqlite3
    from db import persistence
    from db.conversations import SQLiteConversationRepository
    from agent_runtime import ConversationEvent

    original = tmp_path / 'original.db'
    repository = SQLitePartsBinRepository(original)
    service = PartsBinService(repository)
    part = service.add_part(AddPartRequest(PartFields('connector', 'discrete_ic', 3, value='2-pin', package='through-hole')))
    repository.inventory.save_pending_review(part.id, {'description': 'Pending label'}, [])
    store = SQLiteConversationRepository(original)
    store.append(ConversationEvent('user_message', 'history', 'openai', {'text': 'Synthetic retained history'}))
    with repository.transaction() as unit:
        unit.approvals.insert(approval())
    with sqlite3.connect(original) as connection:
        connection.execute('DROP INDEX uq_nominal_passive')
        connection.execute('CREATE UNIQUE INDEX uq_passive ON parts (part_category, value, package) WHERE part_number IS NULL')
    snapshot = tmp_path / 'migration-copy.db'
    with sqlite3.connect(original) as source, sqlite3.connect(snapshot) as destination:
        source.backup(destination)
    def contents(path):
        with sqlite3.connect(path) as connection:
            return {name: connection.execute(f'SELECT * FROM {name} ORDER BY rowid').fetchall()
                    for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    before = contents(original)
    persistence.init_db(snapshot)
    persistence.init_db(snapshot)
    assert contents(snapshot) == before == contents(original)
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        indices = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert 'uq_nominal_passive' in indices and 'uq_passive' not in indices
    copied = PartsBinService(SQLitePartsBinRepository(snapshot))
    distinct = copied.add_part(AddPartRequest(PartFields('connector', 'discrete_ic', 1, value='2-pin', package='through-hole')))
    assert distinct.id != part.id
    assert copied.get(GetPartRequest(part.id)).quantity == 3


def test_concurrent_duplicate_intake_preserves_one_unleased_execution(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    context = {"input_digest": "same-input", "user_text": "hello"}
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: repository.executions.prepare("thread", "request", context), range(2)))
    assert results[0] == results[1]
    original = repository.executions.claim("thread", "request", "worker")
    repository.executions.save("request", "worker", {**original.context, "round": 1}, "waiting_model")
    duplicate = repository.executions.prepare("thread", "request", context)
    assert duplicate.status == "waiting_model"
    assert duplicate.context["round"] == 1
    repository.executions.assert_owned("request", "worker")
    repository.executions.release("request", "worker")
