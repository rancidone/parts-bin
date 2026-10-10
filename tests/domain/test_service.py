import pytest
from db.repository import SQLitePartsBinRepository

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
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"))
    part = service.add_part(AddPartRequest(fields()))
    assert part.value == "10k"
    with pytest.raises(DomainError) as error:
        service.add_part(AddPartRequest(fields()))
    assert error.value.code == ErrorCode.DUPLICATE_PART


def test_add_stock_and_atomic_bulk_update(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"))
    first = service.add_part(AddPartRequest(fields()))
    second = service.add_part(AddPartRequest(fields(value="22k")))
    updated = service.add_stock(AddStockRequest(first.id, 3))
    assert updated.quantity == 8
    result = service.bulk_update(BulkUpdateRequest((first.id, second.id), {"package": "0603"}))
    assert [part.package for part in result] == ["0603", "0603"]


def test_bulk_update_validates_every_selected_part_before_writing(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"))
    first = service.add_part(AddPartRequest(fields()))
    second = service.add_part(AddPartRequest(fields(value="22k")))
    with pytest.raises(DomainError) as error:
        service.bulk_update(BulkUpdateRequest((first.id, second.id), {"quantity": -1}))
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert service.get(GetPartRequest(first.id)).quantity == 5
    assert service.get(GetPartRequest(second.id)).quantity == 5


def test_update_repairs_historical_passive_slot_mixup(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"))
    part = service.add_part(AddPartRequest(fields(value="100n", package="0402")))
    updated = service.update_part(UpdatePartRequest(part.id, {
        "profile": "discrete_ic", "value": "0603", "package": "0603", "part_number": "1uF",
    }))
    assert updated.profile == "passive"
    assert updated.value == "1uf"  # incompatible units are not interpreted as resistance
    assert updated.part_number is None


def test_domain_validation_works_with_injected_storage_without_sqlite():
    from unittest.mock import Mock
    from domain.repositories import InventoryRepository, PartsBinRepository

    repository = Mock(spec=PartsBinRepository)
    repository.inventory = Mock(spec=InventoryRepository)
    service = PartsBinService(repository)
    with pytest.raises(DomainError) as error:
        service.add_part(AddPartRequest(fields(quantity=-1)))
    assert error.value.code == ErrorCode.INVALID_INPUT
    repository.inventory.insert.assert_not_called()
    repository.inventory.search.assert_not_called()


def test_storage_conflicts_are_mapped_to_domain_errors_without_backend_details():
    from unittest.mock import Mock
    from domain.repositories import InventoryRepository, PartsBinRepository, RepositoryConflict

    repository = Mock(spec=PartsBinRepository)
    repository.inventory = Mock(spec=InventoryRepository)
    repository.inventory.search.return_value = []
    repository.inventory.insert.side_effect = RepositoryConflict("backend constraint")
    with pytest.raises(DomainError) as error:
        PartsBinService(repository).add_part(AddPartRequest(fields()))
    assert error.value.code == ErrorCode.DUPLICATE_PART


@pytest.mark.parametrize('category,raw,edited,canonical', [
    ('resistor', '10K', '22K', '22k'),
    ('capacitor', '100nF', '4u7', '4.7u'),
    ('inductor', '10uH', '0.01mH', '0.01m'),
])
def test_edited_passives_are_searchable_and_duplicate_checked(tmp_path, category, raw, edited, canonical):
    from domain import SearchPartsRequest
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    part = service.add_part(AddPartRequest(fields(part_category=category, value=raw)))
    result = service.update_part(UpdatePartRequest(part.id, {'value': edited}))
    assert result.value == canonical
    assert result.quantity == part.quantity
    assert result.created_at == part.created_at
    assert service.search(SearchPartsRequest({'part_category': category, 'value': edited})) == [result]
    assert service.search(SearchPartsRequest({'value': edited})) == [result]
    with pytest.raises(DomainError) as error:
        service.add_part(AddPartRequest(fields(part_category=category, value=edited)))
    assert error.value.code == ErrorCode.DUPLICATE_PART
    incremented = service.add_or_increment(AddPartRequest(fields(part_category=category, value=edited, quantity=2)))
    assert incremented.id == part.id and incremented.quantity == 7


def test_normalization_is_applied_before_injected_storage_calls():
    from unittest.mock import Mock
    from domain import Part, SearchPartsRequest
    from domain.repositories import InventoryRepository, PartsBinRepository
    repository = Mock(spec=PartsBinRepository)
    repository.inventory = Mock(spec=InventoryRepository)
    repository.inventory.search.return_value = []
    repository.inventory.insert.return_value = 1
    service = PartsBinService(repository)
    service.add_part(AddPartRequest(fields(value='5K1')))
    assert repository.inventory.insert.call_args.args[0]['value'] == '5.1k'
    stored = Part(id=1, **vars(fields(value='5.1k')), created_at='created', updated_at='updated')
    repository.inventory.search.return_value = [stored]
    assert service.search(SearchPartsRequest({'part_category': 'resistor', 'value': '5K1'})) == [stored]
    repository.inventory.search.assert_called_with({'part_category': 'resistor'})
    with pytest.raises(DomainError) as error:
        service.add_part(AddPartRequest(fields(value='5K1')))
    assert error.value.code == ErrorCode.DUPLICATE_PART
    assert repository.inventory.insert.call_count == 1


def test_bulk_value_edit_rejects_normalized_collision_without_partial_writes(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    first = service.add_part(AddPartRequest(fields(value='10K')))
    second = service.add_part(AddPartRequest(fields(value='22K')))
    with pytest.raises(DomainError) as error:
        service.bulk_update(BulkUpdateRequest((first.id, second.id), {'value': '2R2'}))
    assert error.value.code == ErrorCode.CONFLICT
    assert service.list() == [first, second]
    # Different packages remain distinct identities, even with the same value.
    service.update_part(UpdatePartRequest(second.id, {'package': '0603'}))
    result = service.bulk_update(BulkUpdateRequest((first.id, second.id), {'value': '2R2'}))
    assert [part.value for part in result] == ['2.2r', '2.2r']


def test_unknown_package_is_not_a_duplicate_wildcard(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    known = service.add_part(AddPartRequest(fields(package='0402')))
    unknown = service.add_part(AddPartRequest(fields(package=None)))
    incremented = service.add_or_increment(AddPartRequest(fields(package=None, quantity=2)))
    assert incremented.id == unknown.id and incremented.quantity == 7
    assert service.get(GetPartRequest(known.id)).quantity == 5


def test_historical_spelling_can_be_searched_without_rewriting_data(tmp_path):
    from domain import SearchPartsRequest
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    # Simulate a value written by the old edit path.
    part_id = repository.inventory.insert(vars(fields(value='22K')))
    before = repository.inventory.get(part_id)
    service = PartsBinService(repository)
    assert service.search(SearchPartsRequest({'part_category': 'resistor', 'value': '22k'})) == [before]
    assert repository.inventory.get(part_id) == before
    with pytest.raises(DomainError) as error:
        service.add_part(AddPartRequest(fields(value='22k')))
    assert error.value.code == ErrorCode.DUPLICATE_PART
    assert repository.inventory.get(part_id) == before


def test_historical_equivalent_records_require_resolution_before_increment(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    first_id = repository.inventory.insert(vars(fields(value='22K')))
    second_id = repository.inventory.insert(vars(fields(value='22k')))
    service = PartsBinService(repository)
    before = service.list()
    with pytest.raises(DomainError) as error:
        service.add_or_increment(AddPartRequest(fields(value='22K')))
    assert error.value.code == ErrorCode.CONFLICT
    assert set(error.value.details['part_ids']) == {first_id, second_id}
    assert service.list() == before


def test_edit_cannot_collide_with_historical_noncanonical_identity(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    historical = repository.inventory.insert(vars(fields(value='22K')))
    service = PartsBinService(repository)
    current = service.add_part(AddPartRequest(fields()))
    before = service.list()
    with pytest.raises(DomainError) as error:
        service.update_part(UpdatePartRequest(current.id, {'value': '22k'}))
    assert error.value.code == ErrorCode.CONFLICT
    assert historical in error.value.details['part_ids']
    assert service.list() == before


def test_batch_add_combines_normalized_values_and_retains_exact_part_suffixes(tmp_path):
    from domain import AddPartsRequest
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    result = service.add_parts(AddPartsRequest((fields(value='2R2'), fields(value='2.2r', quantity=3))))
    assert len(result) == 1 and result[0].quantity == 8
    first = service.add_or_increment(AddPartRequest(fields(
        profile='discrete_ic', part_category='transistor', part_number='PBSS5350T,215', value=None)))
    second = service.add_or_increment(AddPartRequest(fields(
        profile='discrete_ic', part_category='transistor', part_number='PBSS5350T', value=None)))
    assert first.id != second.id
    assert first.part_number == 'PBSS5350T,215'
    assert service.add_or_increment(AddPartRequest(fields(
        profile='discrete_ic', part_category='transistor', part_number='PBSS5350T,215', value=None))).quantity == 10


def test_review_normalizes_value_but_preserves_original_evidence(tmp_path):
    from domain import ApplyReviewRequest, ProvenanceRequest, SearchPartsRequest
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    service = PartsBinService(repository)
    original = service.add_part(AddPartRequest(fields()))
    evidence = {'field_name': 'value', 'field_value': '22K', 'source_tier': 'user',
                'source_kind': 'assertion', 'extraction_method': 'user', 'evidence': 'Marked 22K'}
    repository.inventory.save_pending_review(original.id, {'value': '22K'}, [evidence])
    accepted = service.apply_review(ApplyReviewRequest(original.id))
    assert accepted.value == '22k' and accepted.quantity == original.quantity
    assert service.search(SearchPartsRequest({'part_category': 'resistor', 'value': '22K'})) == [accepted]
    saved = service.provenance(ProvenanceRequest(original.id))[0]
    assert saved['field_value'] == '22K'
    assert saved['evidence'] == 'Marked 22K'
    assert original.id not in service.list_pending_reviews()


def test_conflicting_review_keeps_inventory_evidence_and_pending_proposal(tmp_path):
    from domain import ApplyReviewRequest, ProvenanceRequest
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    service = PartsBinService(repository)
    original = service.add_part(AddPartRequest(fields()))
    service.add_part(AddPartRequest(fields(value='22K')))
    evidence = {'field_name': 'value', 'field_value': '22K', 'source_tier': 'user',
                'source_kind': 'assertion', 'extraction_method': 'user', 'evidence': 'Marked 22K'}
    repository.inventory.save_pending_review(original.id, {'value': '22K'}, [evidence])
    reviews = service.list_pending_reviews()
    with pytest.raises(DomainError) as error:
        service.apply_review(ApplyReviewRequest(original.id))
    assert error.value.code == ErrorCode.CONFLICT
    assert service.get(GetPartRequest(original.id)) == original
    assert service.list_pending_reviews() == reviews
    assert service.provenance(ProvenanceRequest(original.id)) == []


@pytest.mark.parametrize('value', [22, True, ['22k']])
def test_invalid_value_types_are_rejected_before_storage(tmp_path, value):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    with pytest.raises(DomainError) as error:
        service.add_part(AddPartRequest(fields(value=value)))
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert service.list() == []
