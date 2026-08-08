import pytest

from domain import (
    AddPartRequest, AddStockRequest, BulkUpdateRequest, DomainError,
    ErrorCode, GetPartRequest, PartFields, PartsBinService,
    UpdatePartRequest,
)


def fields(**overrides):
    values = {
        "part_category": "resistor", "profile": "passive", "quantity": 5,
        "value": "10K", "package": "0402", "part_number": None,
        "manufacturer": None, "description": None,
    }
    values.update(overrides)
    return PartFields(**values)


def test_add_part_normalizes_and_rejects_duplicate(tmp_path):
    service = PartsBinService(tmp_path / "parts.db")
    part = service.add_part(AddPartRequest(fields()))
    assert part.value == "10k"
    with pytest.raises(DomainError) as error:
        service.add_part(AddPartRequest(fields()))
    assert error.value.code == ErrorCode.DUPLICATE_PART


def test_add_stock_and_atomic_bulk_update(tmp_path):
    service = PartsBinService(tmp_path / "parts.db")
    first = service.add_part(AddPartRequest(fields()))
    second = service.add_part(AddPartRequest(fields(value="22k")))
    updated = service.add_stock(AddStockRequest(first.id, 3))
    assert updated.quantity == 8
    result = service.bulk_update(BulkUpdateRequest((first.id, second.id), {"package": "0603"}))
    assert [part.package for part in result] == ["0603", "0603"]


def test_bulk_update_validates_every_selected_part_before_writing(tmp_path):
    service = PartsBinService(tmp_path / "parts.db")
    first = service.add_part(AddPartRequest(fields()))
    second = service.add_part(AddPartRequest(fields(value="22k")))
    with pytest.raises(DomainError) as error:
        service.bulk_update(BulkUpdateRequest((first.id, second.id), {"quantity": -1}))
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert service.get(GetPartRequest(first.id)).quantity == 5
    assert service.get(GetPartRequest(second.id)).quantity == 5


def test_update_repairs_historical_passive_slot_mixup(tmp_path):
    service = PartsBinService(tmp_path / "parts.db")
    part = service.add_part(AddPartRequest(fields(value="100n", package="0402")))
    updated = service.update_part(UpdatePartRequest(part.id, {
        "profile": "discrete_ic", "value": "0603", "package": "0603", "part_number": "1uF",
    }))
    assert updated.profile == "passive"
    assert updated.value == "1uF"
    assert updated.part_number is None
