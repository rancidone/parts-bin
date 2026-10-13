from copy import deepcopy

import pytest

from domain.specification_conditions import same_conditions
from domain.specifications import evaluate, validate_requirements


@pytest.mark.parametrize('left,right', [
    ('18 °C to 22 °C', '20±2°C'), ('18 to 22 °C', '20 °C ± 2 °C'),
    ('-5 °C to 5 °C', '0±5°C'), ('18.0 – 22.00 °C', '20 ± 2 °C'),
    ('−40°C to +85°C', '-40 to 85 °C'), ('−25 °C', '-25 °C'),
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
    ('−40°C to 85°C', '40 to 85 °C'), ('−20±−2°C', '-22 to -18 °C'),
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


@pytest.mark.parametrize('left,right', [
    ('5 to 36 V', '5000 mV to 0.036 kV'),
    ('5 V to 36 V', '5–36 V'),
    ('5000 to 36000 mV', '5 V — 36 V'),
    ('5.000 V to 36.000 V', '5 to 36 V'),
    ('5 V to 5 V', '5000 mV'),
])
def test_equivalent_explicit_supply_ranges_preserve_originals(left, right):
    source = {'supply_voltage': left, 'ambient_temperature': '25 °C'}
    original = deepcopy(source)
    assert same_conditions(source, {'supply_voltage': right, 'ambient_temperature': '25 °C'})
    assert source == original


@pytest.mark.parametrize('left,right', [
    ('5 to 36 V', '5 V'),
    ('5 to 36 V', '6 to 36 V'),
    ('5 to 36 V', '5 to 30 V'),
    ('5 to 36 V', '4 to 40 V'),
    ('36 to 5 V', '5 to 36 V'),
    ('5 to 36 V typical', '5 to 36 V'),
    ('5 to 36 V (±2.5 V to ±18 V)', '5 to 36 V'),
    ('5 to 36', '5 to 36 V'),
    ('5 A to 36 V', '5 to 36 V'),
    ('-5 V to 36 V', '5 to 36 V'),
])
def test_supply_ranges_do_not_infer_missing_units_qualifiers_or_coverage(left, right):
    assert not same_conditions({'supply_voltage': left}, {'supply_voltage': right})


def test_supply_range_comparison_is_limited_to_the_named_condition():
    assert same_conditions({'input_voltage': '5 to 36 V'}, {'input_voltage': '5000 mV to 36000 mV'})
    assert not same_conditions({'unrecognized_voltage': '5 to 36 V'}, {'unrecognized_voltage': '5000 mV to 36000 mV'})
    assert not same_conditions({'supply_voltage': '5 to 36 V'}, {'input_voltage': '5 to 36 V'})


@pytest.mark.parametrize('key,left,right', [
    ('output_current', '100 mA to 1 A', '0.1 to 1 A'),
    ('collector_current', '-5 mA to 10 mA', '-0.005 to 0.01 A'),
    ('measurement_frequency', '100 kHz to 1 MHz', '0.1 to 1 MHz'),
    ('pulse_duration', '1 µs to 2 ms', '0.001 to 2 ms'),
    ('gate_source_voltage', '5 ± 1 V', '4000 mV to 6 V'),
    ('drain_current', '1 A ± 100 mA', '0.9 to 1.1 A'),
])
def test_electrical_intervals_are_reusable_across_named_dimensions(key, left, right):
    assert same_conditions({key: left}, {key: right})


@pytest.mark.parametrize('key,left,right', [
    ('output_current', '100 mA to 1 A', '0.1 to 1 V'),
    ('measurement_frequency', '1 kHz to 1 MHz', '1 kHz'),
    ('pulse_duration', '1 ms to 2 ms', '1 to 3 ms'),
    ('drain_current', '1 A ± -100 mA', '0.9 to 1.1 A'),
])
def test_electrical_ranges_preserve_dimension_bounds_and_tolerance_sign(key, left, right):
    assert not same_conditions({key: left}, {key: right})


def test_general_intervals_compare_split_tolerance_and_resistance_units():
    assert same_conditions({'output_voltage': '5 V', 'output_voltage_tolerance': '100 mV'},
                           {'output_voltage': '4.9 to 5.1 V'})
    assert same_conditions({'load_resistance': '1 kΩ to 2 kΩ'},
                           {'load_resistance': '1000 Ω to 2000 Ω'})
    assert not same_conditions({'load_resistance': '1 kΩ to 2 kΩ'},
                               {'load_resistance': '1000 Ω to 2000 Ω', 'load_type': 'resistive'})


def test_reusable_interval_parser_preserves_decimal_precision_without_caller_context():
    from decimal import Decimal
    from domain.specification_conditions import interval
    center = '1' * 70
    parsed = interval(center + ' V ± 1 V', 'V')
    assert parsed == (Decimal('1' * 69 + '0'), Decimal('1' * 69 + '2'))
