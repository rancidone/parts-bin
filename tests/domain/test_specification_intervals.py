"""Whole-interval searches over reviewed facts; no live provider calls."""

import pytest

from tests.storage import TestRepository
from domain import DomainError, PartsBinService
from domain.specifications import contract, validate_facts, validate_requirements
from tests.domain.test_specifications import fact, requirement, search, setup


@pytest.mark.parametrize('category,min_name,max_name,conditions', [
    ('linear regulator', 'minimum_input_voltage', 'maximum_input_voltage', {'ambient_temperature': '-40 to 85 °C'}),
    ('buck converter', 'minimum_input_voltage', 'maximum_input_voltage', {'ambient_temperature': '-40 to 85 °C'}),
    ('op amp', 'minimum_supply_voltage', 'maximum_supply_voltage',
     {'ambient_temperature': '-40 to 85 °C', 'supply_convention': 'total rail-to-rail'}),
])
def test_requested_operating_interval_checks_both_endpoints_after_review_restart(tmp_path, category, min_name, max_name, conditions):
    repository, service, part = setup(tmp_path, category)
    facts = [fact(min_name, '2.7 V', 'operating_minimum', conditions=conditions),
             fact(max_name, '5.5 V', 'operating_maximum', conditions=conditions)]
    query = [requirement(facts[0], '4500 mV to 0.0055 kV', 'lte'),
             requirement(facts[1], '4.5 to 5.5 V', 'gte')]
    service.stage_specifications(part, facts)
    assert search(service, category, query)['incomplete_count'] == 1
    restarted = PartsBinService(TestRepository(repository.isolation_key))
    restarted.apply_specification_review(part.id)
    result = search(restarted, category, query)
    assert result['match_count'] == 1
    assert result['matches'][0]['supporting_facts'] == facts
    assert restarted.list()[0].quantity == 4
    for requested in ['2.6 to 5.5 V', '4.5 to 5.6 V']:
        assert search(restarted, category, [requirement(facts[0], requested, 'lte'),
                                          requirement(facts[1], requested, 'gte')])['match_count'] == 0
    different = [{**item, 'conditions': {**item['conditions'], 'ambient_temperature': '-20 to 70 °C'}} for item in query]
    assert search(restarted, category, different)['match_count'] == 1


@pytest.mark.parametrize('stored,requested,comparison,matches,incomplete', [
    ('1 to 2 V', '1000 mV to 0.002 kV', 'eq', 1, 0),
    ('1.5 V ± 0.5 V', '1 to 2 V', 'eq', 1, 0),
    ('1 to 2 V', '1 V', 'gte', 1, 0),
    ('1 to 2 V', '2 V', 'lte', 1, 0),
    ('1 to 2 V', '1.5 V', 'gte', 0, 1),
    ('1 to 2 V', '1.5 V', 'lte', 0, 1),
    ('1 to 2 V', '1.5 V', 'eq', 0, 1),
    ('1 to 2 V', '3 V', 'gte', 0, 0),
    ('1 to 2 V', '0.5 V', 'lte', 0, 0),
    ('1 to 2 V', '3 to 4 V', 'eq', 0, 0),
    ('2 V', '1 to 3 V', 'gte', 0, 0),
    ('2 V', '1 to 3 V', 'lte', 0, 0),
])
def test_threshold_interval_never_silently_selects_one_endpoint(tmp_path, stored, requested, comparison, matches, incomplete):
    _, service, part = setup(tmp_path, 'mosfet')
    proposed = fact('gate_threshold_voltage', stored, 'threshold', conditions={
        'drain_current': '250 µA', 'drain_source_voltage': 'VGS',
        'junction_temperature': '25 °C', 'value_kind': 'min/max range'})
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    result = search(service, 'mosfet', [requirement(proposed, requested, comparison)])
    assert (result['match_count'], result['incomplete_count']) == (matches, incomplete)
    assert service.get_specifications(part.id)['facts'] == [proposed]


@pytest.mark.parametrize('value', ['2 to 1 V', '1 V to 2 A', '-1 to 2 V', '1 V ± 2 V',
                                   '1 V ± -0.5 V', '1 to 2', '1 to 2 V typical'])
def test_invalid_value_intervals_rejected_for_facts_and_requirements(value):
    proposed = fact('gate_threshold_voltage', value, 'threshold')
    with pytest.raises(DomainError):
        validate_facts('mosfet', [proposed], part_number='TEST-EXACT')
    with pytest.raises(DomainError):
        validate_requirements('mosfet', [requirement(proposed)])


def test_passive_identity_range_is_not_a_nominal_value(tmp_path):
    _, service, part = setup(tmp_path)
    with pytest.raises(DomainError, match='Nominal passive identity'):
        service.stage_specifications(part, [fact('resistance', '9 to 11 kΩ', 'nominal')])
    assert service.get_specifications(part.id)['pending_review'] is None
    assert 'operating minimum' in contract('LDO')['numeric_value_policy']


def test_mocked_source_interval_retains_passages_and_reviewable_range():
    from ingestion.electrical_source import validate_source_facts
    from ingestion.supplied_source import Document

    passage = 'TEST-EXACT: VGS(th) minimum 1 V, maximum 2 V at ID 250 µA, VDS=VGS, Tj=25 °C.'
    document = Document('https://example.com/threshold.pdf', 'a' * 64,
                        '2026-10-10T12:00:00+00:00', (passage,))
    raw = {'name': 'gate_threshold_voltage', 'value': '1 to 2 V', 'basis': 'threshold',
           'conditions': [{'name': name, 'value': value} for name, value in {
               'drain_current': '250 µA', 'drain_source_voltage': 'VGS',
               'junction_temperature': '25 °C', 'value_kind': 'min/max range'}.items()],
           'evidence': {'passage_ids': [1]}}
    records = validate_source_facts([raw], 'mosfet', 'TEST-EXACT', document,
                                    [{'page': 1, 'text': passage}])
    assert records[0]['value'] == '1 to 2 V'
    assert records[0]['evidence']['excerpt'] == passage
    assert records[0]['evidence']['sha256'] == document.sha256
