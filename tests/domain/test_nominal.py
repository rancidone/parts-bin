"""Exact nominal storage, dimensions and display are owned by the domain."""
from decimal import localcontext

import pytest

from domain import AddPartRequest, DomainError, GetPartRequest, PartFields, PartsBinService, SearchPartsRequest
from domain.nominal import NominalQuantity, nominal_for_category, parse_nominal
from scripts.normalize_nominal_values import migrate
from tests.storage import TestRepository


@pytest.mark.parametrize('category,raw,decimal,unit,display', [
    ('resistor', '2R2', '2.2', 'Ω', '2R2'),
    ('resistor', '100k', '100000', 'Ω', '100k'),
    ('resistor', '5.1kΩ', '5100', 'Ω', '5k1'),
    ('resistor', '10 kω', '10000', 'Ω', '10k'),
    ('resistor', '1m', '1000000', 'Ω', '1M'),
    ('resistor', '1 mΩ', '0.001', 'Ω', '0R001'),
    ('resistor', '0', '0', 'Ω', '0R'),
    ('resistor', '-0.00', '0', 'Ω', '0R'),
    ('capacitor', '100nF', '0.0000001', 'F', '100nF'),
    ('capacitor', '0.1µF', '0.0000001', 'F', '100nF'),
    ('capacitor', '4u7', '0.0000047', 'F', '4.7µF'),
    ('capacitor', '1mF', '0.001', 'F', '1mF'),
    ('capacitor', '1MF', '1000000', 'F', '1MF'),
    ('capacitor', '1M5', '1500000', 'F', '1.5MF'),
    ('capacitor', '1m5', '0.0015', 'F', '1.5mF'),
    ('inductor', '10uH', '0.00001', 'H', '10µH'),
    ('resettable fuse', '500 mA', '0.5', 'A', '500mA'),
    ('resettable fuse', '0.5 a', '0.5', 'A', '500mA'),
    ('regulator', '3300mV', '3.3', 'V', '3.3V'),
    ('clock oscillator', '16MHz', '16000000', 'Hz', '16MHz'),
    ('power', '250mW', '0.25', 'W', '250mW'),
])
def test_nominal_parse_storage_and_display(category, raw, decimal, unit, display):
    quantity = nominal_for_category(raw, category)
    assert quantity == NominalQuantity(decimal, unit)
    assert quantity.display == display
    assert nominal_for_category(decimal, category, unit) == quantity


@pytest.mark.parametrize('raw', ['10k ±1%', '3–5 V', 'at most 5 V', '24 v dc', '2x5', 'unknown'])
def test_qualifiers_and_non_numeric_labels_are_not_reinterpreted(raw):
    assert parse_nominal(raw) is None


def test_decimal_precision_is_independent_of_context():
    with localcontext() as context:
        context.prec = 3
        assert nominal_for_category('123456.789k', 'resistor').decimal == '123456789'
        assert nominal_for_category('0.000000123456789F', 'capacitor').decimal == '0.000000123456789'


@pytest.fixture
def service(tmp_path):
    return PartsBinService(TestRepository(tmp_path / 'nominal'))


def add(service, value, category='resistor', package='0603', **extra):
    return service.add_part(AddPartRequest(PartFields(category, 'passive', 20, value, package, **extra)))


def test_equivalent_units_share_identity_but_dimensions_do_not(service):
    first = add(service, '100nF', 'capacitor')
    assert first.value == '0.0000001' and first.value_unit == 'F' and first.display_value == '100nF'
    with pytest.raises(DomainError, match='identical'):
        add(service, '0.1µF', 'capacitor')
    volts = add(service, '500mV', 'module', part_number='MODULE-V')
    amps = add(service, '500mA', 'module', part_number='MODULE-A')
    assert volts.value == amps.value == '0.5'
    assert service.search(SearchPartsRequest({'value': '500mV'})) == [volts]
    assert service.search(SearchPartsRequest({'value': '0.5', 'value_unit': 'A'})) == [amps]
    with pytest.raises(DomainError, match='unit'):
        add(service, '3 V', 'capacitor', value_unit='F')


def test_migration_preserves_stock_reviews_evidence_and_is_idempotent(service, tmp_path):
    repository = service.repository
    identifier = repository.inventory.insert(vars(PartFields('resistor', 'passive', 12, '5.1k', '0603')))
    evidence = {'field_name': 'value', 'field_value': '5K1', 'source_tier': 'user',
                'source_kind': 'assertion', 'extraction_method': 'user', 'evidence': 'Marked 5K1'}
    repository.inventory.save_pending_review(identifier, {'value': '5K1'}, [evidence])
    before = service.get(GetPartRequest(identifier))
    reviews = service.list_pending_reviews()
    backup = tmp_path / 'before.json'
    plan = migrate(repository, backup=backup)
    assert plan['applied'] and len(plan['changes']) == 1
    after = service.get(GetPartRequest(identifier))
    assert after.value == '5100' and after.value_unit == 'Ω'
    assert after.quantity == before.quantity and after.id == before.id and after.created_at == before.created_at
    assert service.list_pending_reviews() == reviews
    assert backup.stat().st_mode & 0o777 == 0o600
    assert service.normalize_nominal_values()['changes'] == []


def test_migration_never_merges_equivalent_legacy_stock(service, tmp_path):
    repository = service.repository
    first = repository.inventory.insert(vars(PartFields('capacitor', 'passive', 12, '100n', '0603')))
    second = repository.inventory.insert(vars(PartFields('capacitor', 'passive', 34, '0.1u', '0603')))
    before = service.list()
    backup = tmp_path / 'blocked.json'
    plan = migrate(repository, backup=backup)
    assert not plan['applied'] and {frozenset(ids) for ids in plan['conflicting_part_ids']} == {frozenset({first, second})}
    assert service.list() == before and not backup.exists()


def test_migration_rebinds_pending_specification_snapshot_without_rewriting_facts(service):
    from tests.domain.test_specifications import fact
    repository = service.repository
    identifier = repository.inventory.insert(vars(PartFields('resistor', 'passive', 12, '5.1k', '0603', part_number='EXACT-R')))
    original = service.get(GetPartRequest(identifier))
    facts = [fact('rated_power', '250 mW', 'rated', number='EXACT-R')]
    service.stage_specifications(original, facts)
    before = service.get_specifications(identifier)['pending_review']['facts']
    service.normalize_nominal_values()
    assert service.get_specifications(identifier)['pending_review']['facts'] == before
    service.apply_specification_review(identifier)
    assert service.get_specifications(identifier)['facts'] == before


def test_applying_nominal_review_persists_derived_unit_and_original_evidence(service):
    from domain import ApplyReviewRequest, ProvenanceRequest
    part = add(service, '10k')
    evidence = {'field_name': 'value', 'field_value': '5K1', 'source_tier': 'user',
                'source_kind': 'assertion', 'extraction_method': 'user', 'evidence': 'Marked 5K1'}
    service.repository.inventory.save_pending_review(part.id, {'value': '5K1'}, [evidence])
    updated = service.apply_review(ApplyReviewRequest(part.id))
    assert (updated.value, updated.value_unit, updated.display_value) == ('5100', 'Ω', '5k1')
    assert service.provenance(ProvenanceRequest(part.id))[0]['field_value'] == '5K1'
