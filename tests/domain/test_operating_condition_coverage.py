"""Operating coverage retains complete evidence, qualifiers and both endpoints."""

from copy import deepcopy

import pytest

from tests.storage import TestRepository
from domain import PartsBinService
from domain.specification_conditions import conditions_cover, same_conditions
from tests.domain.test_specifications import fact, requirement, search, setup
from tools import PartsBinToolRegistry


@pytest.mark.parametrize('source,requested', [
    ({'ambient_temperature': '-40 to 85 °C'}, {'ambient_temperature': '25 °C'}),
    ({'ambient_temperature': '-40 to 85 °C'}, {'ambient_temperature': '-40 to 85 °C'}),
    ({'ambient_temperature': '-40 to 85 °C'}, {'ambient_temperature': '-20 to 70 °C'}),
    ({'input_voltage': '3 to 36 V'}, {'input_voltage': '5000 mV ± 100 mV'}),
    ({'output_current': '0 to 1 A'}, {'output_current': '0 to 1000 mA'}),
    ({'measurement_frequency': '1 to 2 MHz'}, {'measurement_frequency': '1500 kHz'}),
    ({'load_resistance': '1 kΩ ± 100 Ω'}, {'load_resistance': '950 Ω to 1 kΩ'}),
    ({'ambient_temperature': '20 °C', 'ambient_temperature_tolerance': '2 °C'},
     {'ambient_temperature': '19 to 21 °C'}),
])
def test_reusable_explicit_containment(source, requested):
    original = deepcopy((source, requested))
    assert conditions_cover(source, requested)
    assert (source, requested) == original
    assert not conditions_cover(requested, source) or same_conditions(source, requested)


@pytest.mark.parametrize('source,requested', [
    ({'ambient_temperature': '-40 to 85 °C'}, {'ambient_temperature': '-41 to 85 °C'}),
    ({'ambient_temperature': '-40 to 85 °C'}, {'ambient_temperature': '25 to 86 °C'}),
    ({'ambient_temperature': '-40 to 85 °C'}, {'junction_temperature': '25 °C'}),
    ({'ambient_temperature': '-40 to 85 °C'}, {}),
    ({}, {'ambient_temperature': '25 °C'}),
    ({'ambient_temperature': '25 °C'}, {'ambient_temperature': '-40 to 85 °C'}),
    ({'unknown': '1 to 2 V'}, {'unknown': '1.5 V'}),
    ({'input_voltage': 'VIN'}, {'input_voltage': '5 V'}),
    ({'output_current': '0 to 1 A'}, {'output_current': '1 V'}),
    ({'input_voltage': '3 to 36 V typical'}, {'input_voltage': '5 V'}),
    ({'load_type': 'resistive', 'ambient_temperature': '-40 to 85 °C'}, {'ambient_temperature': '25 °C'}),
    ({'ambient_temperature': '-40 to 85 °C'}, {'ambient_temperature': '25 °C', 'load_type': 'resistive'}),
])
def test_coverage_never_discards_conditions_or_extrapolates(source, requested):
    assert not conditions_cover(source, requested)


@pytest.mark.parametrize('category,min_name,max_name,qualifiers', [
    ('linear regulator', 'minimum_input_voltage', 'maximum_input_voltage', {}),
    ('buck converter', 'minimum_input_voltage', 'maximum_input_voltage', {}),
    ('op amp', 'minimum_supply_voltage', 'maximum_supply_voltage', {'supply_convention': 'total rail-to-rail'}),
    ('transformer', 'minimum_frequency', 'maximum_frequency', {'primary_rated_voltage': '230 V'}),
])
async def test_tool_search_covers_operating_conditions_after_review_restart(tmp_path, category, min_name, max_name, qualifiers):
    repository, service, part = setup(tmp_path, category)
    units = 'Hz' if category == 'transformer' else 'V'
    facts = [fact(min_name, '3 ' + units, 'operating_minimum', conditions={**qualifiers, 'ambient_temperature': '-40 to 85 °C'}),
             fact(max_name, '36 ' + units, 'operating_maximum', conditions={**qualifiers, 'ambient_temperature': '-40 to 85 °C'})]
    before = deepcopy(facts)
    requirements = [{**requirement(item, '5 to 6 ' + units, op), 'conditions': {**qualifiers, 'ambient_temperature': '25 °C'}}
                    for item, op in zip(facts, ['lte', 'gte'])]
    args = {'filters': {'part_category': category}, 'requirements': requirements}
    service.stage_specifications(part, facts)
    registry = PartsBinToolRegistry(service)
    assert (await registry.execute('search_parts', args))['result']['incomplete_count'] == 1
    restarted = PartsBinService(TestRepository(repository.isolation_key))
    restarted.apply_specification_review(part.id)
    registry = PartsBinToolRegistry(restarted)
    result = (await registry.execute('search_parts', args))['result']
    assert result['match_count'] == 1
    assert result['matches'][0]['supporting_facts'] == before
    assert restarted.list()[0].quantity == 4
    for temperature in ['-41 °C', '86 °C', '-50 to 90 °C']:
        outside = [{**item, 'conditions': {**qualifiers, 'ambient_temperature': temperature}} for item in requirements]
        assert search(restarted, category, outside)['incomplete_count'] == 1
    assert restarted.get_specifications(part.id)['facts'] == before


@pytest.mark.parametrize('category,name,basis,conditions', [
    ('op amp', 'absolute_maximum_supply_voltage', 'absolute_maximum', {'supply_convention': 'total rail-to-rail'}),
    ('op amp', 'gain_bandwidth_product', 'typical', {'supply_voltage': '5 V'}),
    ('op amp', 'input_offset_voltage', 'maximum_magnitude', {'supply_voltage': '5 V', 'common_mode_voltage': '0 V'}),
    ('resistor', 'rated_power', 'rated', {}),
    ('mosfet', 'gate_threshold_voltage', 'threshold', {'drain_current': '250 µA', 'drain_source_voltage': 'VGS', 'value_kind': 'min/max'}),
])
def test_other_bases_retain_equal_conditions(tmp_path, category, name, basis, conditions):
    from domain.specifications import definition
    _, service, part = setup(tmp_path, category)
    temperature = 'junction_temperature' if category == 'mosfet' else 'ambient_temperature'
    proposed = fact(name, '1 ' + definition(category, name).unit, basis,
                    conditions={**conditions, temperature: '-40 to 85 °C'})
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    query = [{**requirement(proposed), 'conditions': {**conditions, temperature: '25 °C'}}]
    assert search(service, category, query)['incomplete_count'] == 1
    assert search(service, category, [requirement(proposed)])['match_count'] == 1


@pytest.mark.parametrize('problem', ['user_assertion', 'missing_endpoint', 'different_qualifier', 'different_source_range'])
def test_operating_coverage_still_requires_both_source_backed_qualified_endpoints(tmp_path, problem):
    _, service, part = setup(tmp_path, 'op amp')
    conditions = {'ambient_temperature': '-40 to 85 °C', 'supply_convention': 'total rail-to-rail'}
    facts = [fact('minimum_supply_voltage', '3 V', 'operating_minimum', conditions=conditions),
             fact('maximum_supply_voltage', '36 V', 'operating_maximum', conditions=conditions)]
    requirements = [{**requirement(item, '5 V', op), 'conditions': {**conditions, 'ambient_temperature': '25 °C'}}
                    for item, op in zip(facts, ['lte', 'gte'])]
    if problem == 'user_assertion':
        facts[1]['evidence'] = {'kind': 'user_assertion', 'excerpt': 'User says 36 V.'}
    elif problem == 'missing_endpoint':
        facts.pop()
    elif problem == 'different_qualifier':
        facts[1]['conditions'] = {**conditions, 'supply_convention': 'per rail'}
    else:
        facts[1]['conditions'] = {**conditions, 'ambient_temperature': '-40 to 20 °C'}
    service.stage_specifications(part, facts)
    service.apply_specification_review(part.id)
    result = search(service, 'op amp', requirements)
    assert result['match_count'] == 0
    assert result['incomplete_count'] == 1
    assert result['incomplete'][0]['missing_or_unqualified'] == ['maximum_supply_voltage']
