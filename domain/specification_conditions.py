"""Conservative comparison of explicit condition quantities; originals stay stored."""

from decimal import Decimal, localcontext
import re

_NUMBER = r'([+-]?(?:\d+(?:\.\d+)?|\.\d+))'
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


def _quantity(raw: str, unit: str) -> Decimal | None:
    """Read one signed quantity with explicit units, without changing its text."""
    match = re.fullmatch(rf'\s*{_NUMBER}\s*([^\s]+)\s*', raw)
    if match is None:
        return None
    number, symbol = match.groups()
    if unit in {'°C', '%'}:
        return Decimal(number) if symbol == unit else None
    from .specifications import numeric_value
    from .errors import DomainError
    try:
        value = numeric_value(number.lstrip('+-') + ' ' + symbol, unit)
        return value.copy_negate() if number.startswith('-') else value
    except DomainError:
        return None


def interval(raw: str, unit: str) -> tuple[Decimal, Decimal] | None:
    """Parse a scalar, explicit interval or center/tolerance in one dimension.

    A trailing unit applies to both endpoints when the first unit is omitted.
    Explicit units on both quantities are converted independently. Qualifiers,
    relations and malformed intervals stay unparsed.
    """
    with localcontext() as context:
        context.prec = 220
        return _interval(raw, unit)


def _interval(raw: str, unit: str) -> tuple[Decimal, Decimal] | None:
    raw = raw.replace('−', '-')
    point = _quantity(raw, unit)
    if point is not None:
        return (point, point)
    tolerance = '±' in raw
    pieces = raw.split('±') if tolerance else re.split(r'\s*(?:\bto\b|–|—)\s*', raw.strip())
    if len(pieces) != 2:
        return None
    first, second = (piece.strip() for piece in pieces)
    if re.fullmatch(_NUMBER, first):
        suffix = re.fullmatch(rf'\s*{_NUMBER}\s*([^\s]+)\s*', second)
        if suffix is None:
            return None
        first += ' ' + suffix[2]
    left, right = _quantity(first, unit), _quantity(second, unit)
    if left is None or right is None:
        return None
    if tolerance:
        return (left - right, left + right) if right >= 0 else None
    return (left, right) if left <= right else None


def comparable_conditions(raw):
    result = dict(raw)
    if 'voltage_type' in result and 'current_type' not in result:
        result['current_type'] = result.pop('voltage_type')
    load = result.get('load_type', '').casefold()
    if load in {'resistive', 'resistive load'}:
        result['load_type'] = 'resistive'
    for key, unit in _CONDITION_UNITS.items():
        if key not in result:
            continue
        value = interval(result[key], unit)
        tolerance_key = key + '_tolerance'
        if tolerance_key in result:
            delta = interval(result[tolerance_key], unit)
            # Combine only explicit scalar center and scalar nonnegative tolerance.
            if value is None or value[0] != value[1] or delta is None or delta[0] != delta[1] or delta[0] < 0:
                continue
            value = (value[0] - delta[0], value[0] + delta[0])
            del result[tolerance_key]
        if value is not None:
            result[key] = (unit, *value)
    return result


def same_conditions(left, right):
    # Condition values are bounded to 200 characters by the domain contract.
    with localcontext() as context:
        context.prec = 220
        return comparable_conditions(left) == comparable_conditions(right)
