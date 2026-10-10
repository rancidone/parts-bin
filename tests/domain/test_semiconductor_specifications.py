"""Synthetic facts exercise the contract; they do not measure live extraction."""

import pytest

from domain import DomainError, GetPartRequest, IngestDatasheetRequest, UpdatePartRequest
from domain.specifications import contract
from tests.domain.test_specifications import fact, requirement, search, setup


QUALIFIED = [
    ('bjt', 'dc_current_gain', '110 ratio', 'minimum', {
        'collector_current': '2 mA', 'collector_emitter_voltage': '5 V', 'ambient_temperature': '25 °C'}),
    ('bjt', 'pulsed_collector_current', '1 A', 'absolute_maximum_pulsed', {
        'pulse_duration': '1 ms', 'ambient_temperature': '25 °C'}),
    ('mosfet', 'on_resistance', '5 Ω', 'maximum', {
        'gate_source_voltage': '10 V', 'drain_current': '500 mA', 'junction_temperature': '25 °C'}),
    ('mosfet', 'gate_threshold_voltage', '2.5 V', 'threshold', {
        'drain_current': '0.25 mA', 'drain_source_voltage': 'VGS',
        'junction_temperature': '25 °C', 'value_kind': 'maximum'}),
    ('mosfet', 'continuous_drain_current', '300 mA', 'absolute_maximum_continuous', {
        'gate_source_voltage': '10 V', 'solder_point_temperature': '25 °C'}),
    ('diode', 'forward_voltage', '1 V', 'maximum', {
        'forward_current': '10 mA', 'junction_temperature': '25 °C'}),
    ('diode', 'reverse_leakage_current', '25 nA', 'maximum', {
        'reverse_voltage': '20 V', 'junction_temperature': '25 °C'}),
    ('diode', 'junction_capacitance', '4 pF', 'maximum', {
        'reverse_voltage': '0 V', 'measurement_frequency': '1 MHz', 'junction_temperature': '25 °C'}),
    ('diode', 'continuous_forward_current', '200 mA', 'absolute_maximum_continuous', {
        'ambient_temperature': '25 °C', 'mounting': 'FR4; lead length 10 mm'}),
    ('diode', 'surge_forward_current', '1 A', 'absolute_maximum_nonrepetitive_peak', {
        'pulse_duration': '1 ms', 'waveform': 'square wave', 'junction_temperature': '25 °C before surge'}),
    ('diode', 'reverse_recovery_time', '4 ns', 'maximum', {
        'forward_current': '10 mA', 'reverse_current': '60 mA',
        'recovery_endpoint_current': '1 mA', 'load_resistance': '100 Ω', 'junction_temperature': '25 °C'}),
]


@pytest.mark.parametrize('category,name,value,basis,conditions', QUALIFIED)
def test_reviewed_semiconductor_conditions_gate_matching(tmp_path, category, name, value, basis, conditions):
    repository, service, part = setup(tmp_path, category)
    proposed = fact(name, value, basis, conditions=conditions)
    service.stage_specifications(part, [proposed])
    assert search(service, category, [requirement(proposed)])['incomplete_count'] == 1
    service.apply_specification_review(part.id)
    assert search(service, category, [requirement(proposed)])['match_count'] == 1
    assert service.get(GetPartRequest(part.id)).quantity == 4
    for key in conditions:
        incomplete = {**proposed, 'conditions': {k: v for k, v in conditions.items() if k != key}}
        service.stage_specifications(service.get(GetPartRequest(part.id)), [incomplete])
        service.apply_specification_review(part.id)
        assert search(service, category, [requirement(proposed)])['incomplete_count'] == 1
        if key == 'mounting':
            continue  # Preserved when supplied; not a universally required qualifier.
        result = search(service, category, [requirement(incomplete)])
        assert result['incomplete_count'] == 1
        assert result['matches'] == []
    assert repository.inventory.specifications(part.id)[0]['evidence'] == proposed['evidence']


@pytest.mark.parametrize('stored,query', [
    ('bipolar junction transistor', 'BJT'), ('bjts', 'bipolar transistor'),
    ('MOSFETs', 'mosfet'), ('diodes', 'diode'),
])
def test_semiconductor_aliases_do_not_rewrite_stock(tmp_path, stored, query):
    _, service, part = setup(tmp_path, stored)
    assert contract(stored)['fields'] == contract(query)['fields']
    name, definition = next(iter(contract(query)['fields'].items()))
    value = definition['choices'][0] if definition['choices'] else '100 V'
    proposed = fact(name, value, definition['basis'], conditions={})
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    assert search(service, query, [requirement(proposed, comparison='eq')])['match_count'] == 1
    assert service.get(GetPartRequest(part.id)).part_category == stored


@pytest.mark.parametrize('category', ['bjt', 'mosfet'])
async def test_generic_transistor_requires_explicit_classification(tmp_path, category):
    _, service, part = setup(tmp_path, 'transistor')
    assert not contract('transistor')['supported']
    with pytest.raises(DomainError, match='Clarify the transistor subtype'):
        await service.ingest_datasheet(IngestDatasheetRequest(part.id, 'https://example.com/part.pdf'))
    assert service.get(GetPartRequest(part.id)) == part
    classified = service.update_part(UpdatePartRequest(part.id, {'part_category': category}))
    assert classified.quantity == part.quantity
    assert classified.part_number == part.part_number
    assert contract(classified.part_category)['supported']


def test_diode_peak_voltage_and_surge_do_not_confirm_continuous_ratings(tmp_path):
    _, service, part = setup(tmp_path, 'diode')
    peak = fact('repetitive_peak_reverse_voltage', '100 V', 'absolute_maximum_repetitive_peak', conditions={})
    surge = fact('surge_forward_current', '4 A', 'absolute_maximum_nonrepetitive_peak', conditions={
        'pulse_duration': '1 µs', 'waveform': 'square wave', 'junction_temperature': '25 °C'})
    service.stage_specifications(part, [peak, surge])
    service.apply_specification_review(part.id)
    continuous = fact('continuous_forward_current', '1 A', 'absolute_maximum_continuous')
    reverse = fact('reverse_voltage', '50 V', 'absolute_maximum_continuous', conditions={})
    result = search(service, 'diode', [requirement(continuous), requirement(reverse)])
    assert result['matches'] == []
    assert result['incomplete'][0]['missing_or_unqualified'] == ['continuous_forward_current', 'reverse_voltage']
