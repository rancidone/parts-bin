"""Conservative comparison of explicit condition quantities; originals stay stored."""

from collections.abc import Mapping
from decimal import Decimal, localcontext

from .quantities import contains_interval, interval

# Only named dimensions are interpreted; unknown conditions remain literal.
_CONDITION_UNITS = {
    'ambient_temperature': '°C', 'case_temperature': '°C',
    'rating_temperature': '°C', 'measurement_temperature': '°C',
    'junction_temperature': '°C', 'lead_temperature': '°C',
    'solder_point_temperature': '°C', 'ambient_humidity': '%', 'duty_cycle': '%',
    'forward_current': 'A', 'output_current': 'A', 'collector_current': 'A',
    'drain_current': 'A', 'base_current': 'A', 'reverse_current': 'A',
    'recovery_endpoint_current': 'A',
    'input_voltage': 'V', 'output_voltage': 'V', 'supply_voltage': 'V',
    'gate_source_voltage': 'V', 'drain_source_voltage': 'V',
    'collector_emitter_voltage': 'V', 'reverse_voltage': 'V', 'common_mode_voltage': 'V',
    'measurement_frequency': 'Hz', 'pulse_duration': 's', 'load_resistance': 'Ω',
}


ConditionValue = str | tuple[str, Decimal, Decimal]


def comparable_conditions(raw: Mapping[str, str]) -> dict[str, ConditionValue]:
    result: dict[str, ConditionValue] = dict(raw)
    if 'voltage_type' in result and 'current_type' not in result:
        result['current_type'] = result.pop('voltage_type')
    load = raw.get('load_type', '').casefold()
    if load in {'resistive', 'resistive load'}:
        result['load_type'] = 'resistive'
    for key, unit in _CONDITION_UNITS.items():
        if key not in result:
            continue
        value = interval(raw[key], unit)
        tolerance_key = key + '_tolerance'
        if tolerance_key in result:
            delta = interval(raw[tolerance_key], unit)
            # Combine only explicit scalar center and scalar nonnegative tolerance.
            if value is None or value[0] != value[1] or delta is None or delta[0] != delta[1] or delta[0] < 0:
                continue
            value = (value[0] - delta[0], value[0] + delta[0])
            del result[tolerance_key]
        if value is not None:
            result[key] = (unit, *value)
    return result


def same_conditions(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    # Condition values are bounded to 200 characters by the domain contract.
    with localcontext() as context:
        context.prec = 220
        return comparable_conditions(left) == comparable_conditions(right)


def conditions_cover(available: Mapping[str, str], requested: Mapping[str, str]) -> bool:
    """Cover explicit named intervals while requiring every qualifier on both sides.

    Call only for facts whose basis establishes operating bounds over the supplied
    conditions. Numeric containment alone cannot establish that source semantics.
    Unknown conditions and relationships require literal equality.
    """
    with localcontext() as context:
        context.prec = 220
        left, right = comparable_conditions(available), comparable_conditions(requested)
        if left.keys() != right.keys():
            return False
        for key, value in left.items():
            target = right[key]
            if isinstance(value, tuple) and isinstance(target, tuple):
                if value[0] != target[0] or not contains_interval((value[1], value[2]), (target[1], target[2])):
                    return False
            elif value != target:
                return False
        return True
