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
