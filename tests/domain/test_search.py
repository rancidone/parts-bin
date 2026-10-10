from decimal import Decimal, localcontext

import pytest

from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, ApplyReviewRequest, DomainError, PartFields, PartsBinService, SearchPartsRequest
from domain.search import nominal_value, values_match


@pytest.mark.parametrize('category,stored,requested', [
    ('resistor', '10k', '10000 ohms'),
    ('resistor', '10000r', '10 kΩ'),
    ('resistor', '5K1', '5100 Ω'),
    ('resistor', '1m', '1 MΩ'),
    ('resistor', '0.001r', '1 mΩ'),
    ('capacitor', '100n', '0.1 µF'),
    ('capacitor', '0.1uF', '100 nF'),
    ('capacitor', '4u7', '4700 nF'),
    ('capacitor', '1u', '1 μF'),
    ('inductor', '0.01mH', '10 µH'),
])
def test_nominal_unit_equivalence(category, stored, requested):
    assert values_match(stored, requested, category)
    assert values_match(requested, stored, category)


@pytest.mark.parametrize('category,stored,requested', [
    ('resistor', '1m', '1 mΩ'),
    ('resistor', '10k', '10 µF'),
    ('capacitor', '100n', '100'),
    ('capacitor', '100n', '100 nH'),
    ('inductor', '10u', '10 nH'),
    ('resistor', '10k', '1 0k'),
    ('resistor', '10k', '10k ±1%'),
    ('resistor', '10k', '9k-11k'),
])
def test_unknown_or_different_values_do_not_satisfy_nominal_match(category, stored, requested):
    assert not values_match(stored, requested, category)


def test_unknown_values_are_exact_spelling_matches_only():
    assert values_match('unknown', 'unknown', 'resistor')
    assert nominal_value('unknown', 'resistor') is None


def test_comparison_does_not_round_with_decimal_context():
    with localcontext() as context:
        context.prec = 3
        assert nominal_value('123456.789k', 'resistor') == Decimal('123456789')
        assert not values_match('123456.789k', '123456.788k', 'resistor')


def add(service, **overrides):
    fields = dict(part_category='capacitor', profile='passive', quantity=4,
                  value='100nF', package='0603')
    fields.update(overrides)
    return service.add_part(AddPartRequest(PartFields(**fields)))


def test_combined_lookup_preserves_distinct_stock_and_ignores_pending_values(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    service = PartsBinService(repository)
    first = add(service)
    equivalent = add(service, value='0.1uF')
    add(service, package='0402')
    add(service, value='100000pF', quantity=3)
    add(service, value=None, package='0805')
    pending = add(service, value='220nF')
    repository.inventory.save_pending_review(pending.id, {'value': '0.100uF'}, [{
        'field_name': 'value', 'field_value': '0.100uF', 'source_tier': 'user',
        'source_kind': 'assertion', 'extraction_method': 'user', 'evidence': 'Marked 0.100uF',
    }])
    before = service.list()
    query = SearchPartsRequest({'value': '0.1 µF', 'package': '0603'}, minimum_quantity=4)
    assert {part.id for part in service.search(query)} == {first.id, equivalent.id}
    assert service.list() == before
    assert first.id != equivalent.id  # nominal equivalence does not merge stock identities
    assert service.add_or_increment(AddPartRequest(
        PartFields('capacitor', 'passive', 2, value='0.1uF', package='0603'))).id == equivalent.id
    assert repository.inventory.get(first.id) == first
    accepted = service.apply_review(ApplyReviewRequest(pending.id))
    assert accepted in service.search(query)


@pytest.mark.parametrize('quantity', [-1, True, 1.5, '4', None])
def test_stock_requirement_is_domain_validated(tmp_path, quantity):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    with pytest.raises(DomainError, match='minimum_quantity'):
        service.search(SearchPartsRequest(minimum_quantity=quantity))


def test_exact_ordering_suffix_and_zero_stock(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    full = add(service, part_category='transistor', profile='discrete_ic', value=None,
               part_number='PBSS5350T,215', quantity=0)
    add(service, part_category='transistor', profile='discrete_ic', value=None,
        part_number='PBSS5350T', quantity=10)
    filters = {'part_number': 'PBSS5350T,215'}
    assert service.search(SearchPartsRequest(filters)) == [full]
    assert service.search(SearchPartsRequest(filters, minimum_quantity=1)) == []
