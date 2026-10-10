"""Conservative comparison of explicit condition quantities; originals stay stored."""

from decimal import Decimal, localcontext
import re

_NUMBER = r'([+-]?(?:\d+(?:\.\d+)?|\.\d+))'
_UNITS = {'ambient_temperature': '°C', 'case_temperature': '°C',
          'rating_temperature': '°C', 'measurement_temperature': '°C',
          'ambient_humidity': '%'}


def interval(raw: str, unit: str):
    symbol = re.escape(unit)
    point = re.fullmatch(rf'\s*{_NUMBER}\s*{symbol}\s*', raw)
    if point:
        value = Decimal(point[1])
        return (value, value)
    tolerance = re.fullmatch(rf'\s*{_NUMBER}\s*(?:{symbol})?\s*±\s*{_NUMBER}\s*{symbol}\s*', raw)
    if tolerance:
        center, delta = Decimal(tolerance[1]), Decimal(tolerance[2])
        return (center - delta, center + delta) if delta >= 0 else None
    bounds = re.fullmatch(rf'\s*{_NUMBER}\s*(?:{symbol})?\s*(?:to|–|—)\s*{_NUMBER}\s*{symbol}\s*', raw)
    if bounds:
        lower, upper = Decimal(bounds[1]), Decimal(bounds[2])
        return (lower, upper) if lower <= upper else None
    return None


def comparable_conditions(raw):
    result = dict(raw)
    if 'voltage_type' in result and 'current_type' not in result:
        result['current_type'] = result.pop('voltage_type')
    load = result.get('load_type', '').casefold()
    if load in {'resistive', 'resistive load'}:
        result['load_type'] = 'resistive'
    for key, unit in _UNITS.items():
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
            result[key] = value
    return result


def same_conditions(left, right):
    # Condition values are bounded to 200 characters by the domain contract.
    with localcontext() as context:
        context.prec = 220
        return comparable_conditions(left) == comparable_conditions(right)
