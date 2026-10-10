from copy import deepcopy

import pytest

from domain.specification_conditions import same_conditions
from domain.specifications import evaluate, validate_requirements


@pytest.mark.parametrize('left,right', [
    ('18 °C to 22 °C', '20±2°C'), ('18 to 22 °C', '20 °C ± 2 °C'),
    ('-5 °C to 5 °C', '0±5°C'), ('18.0 – 22.00 °C', '20 ± 2 °C'),
])
def test_equivalent_explicit_intervals_preserve_original_evidence(left, right):
    fact = {'name': 'rated_current', 'value': '0.1 A', 'basis': 'rated',
            'conditions': {'ambient_temperature': left, 'ambient_humidity': '60 % to 70 %'},
            'evidence': {'kind': 'source', 'excerpt': 'Original source'}}
    original = deepcopy(fact)
    requirements = validate_requirements('switch', [{
        'name': 'rated_current', 'value': '0.1 A', 'basis': 'rated', 'comparison': 'eq',
        'conditions': {'ambient_temperature': right, 'ambient_humidity': '65±5%'}}])
    assert evaluate('switch', [fact], requirements) == (True, [], [fact])
    assert fact == original


def test_split_center_and_tolerance_match_but_additional_conditions_stay_required():
    split = {'ambient_temperature': '20 °C', 'ambient_temperature_tolerance': '2 °C'}
    assert same_conditions(split, {'ambient_temperature': '18 °C to 22 °C'})
    assert not same_conditions(split | {'load_type': 'resistive'}, {'ambient_temperature': '18 °C to 22 °C'})


@pytest.mark.parametrize('left,right', [
    ('20±2°C', '20 °C'), ('20±2°C', '19 °C to 22 °C'), ('room temperature', '25 °C'),
    ('20±-2°C', '18 °C to 22 °C'), ('22 °C to 18 °C', '20±2°C'),
    ('20 °C typical', '20 °C'), ('20±2°C', '68±3.6°F'),
])
def test_no_derating_containment_or_inferred_temperature(left, right):
    assert not same_conditions({'ambient_temperature': left}, {'ambient_temperature': right})


def test_condition_names_and_unknown_values_remain_literal():
    assert not same_conditions({'case_temperature': '20±2°C'}, {'ambient_temperature': '18 °C to 22 °C'})
    assert not same_conditions({'unknown': '20±2°C'}, {'unknown': '18 °C to 22 °C'})


def test_explicit_load_spelling_equivalence_does_not_change_other_loads():
    assert same_conditions({'load_type': 'Resistive load'}, {'load_type': 'resistive'})
    assert not same_conditions({'load_type': 'inductive load'}, {'load_type': 'resistive'})


def test_source_capacitor_missing_dc_qualifier_remains_incomplete():
    fact = {'name': 'rated_voltage', 'value': '500 V', 'basis': 'rated',
            'conditions': {'rating_temperature': '85 °C'}, 'evidence': {'kind': 'source'}}
    requirements = validate_requirements('capacitor', [{
        'name': 'rated_voltage', 'value': '500 V', 'basis': 'rated', 'comparison': 'eq',
        'conditions': {'rating_temperature': '85 °C'}}])
    assert evaluate('capacitor', [fact], requirements) == (True, ['rated_voltage'], [])
    fact['conditions']['voltage_type'] = 'DC'
    requirements[0]['conditions']['current_type'] = 'DC'
    assert evaluate('capacitor', [fact], requirements) == (True, [], [fact])
