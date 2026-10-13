"""Exact unit-aware quantities and intervals shared by inventory and evidence checks."""

from decimal import Decimal, InvalidOperation, localcontext
import re

from .errors import DomainError, ErrorCode


def invalid(message: str) -> None:
    raise DomainError(ErrorCode.INVALID_INPUT, message)


# SI prefixes are case-sensitive. Ratio and percent have no prefixes.
_PREFIXES = {'': 0, 'p': -12, 'n': -9, 'u': -6, 'µ': -6, 'μ': -6, 'm': -3, 'k': 3, 'M': 6, 'G': 9}


def numeric_value(raw: str, unit: str) -> Decimal:
    if not isinstance(raw, str) or len(raw) > 100:
        invalid('Numeric specifications require a bounded decimal string with units')
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*([^\s]+)\s*', raw)
    if match is None:
        invalid(f'Use an explicit decimal value and {unit} unit')
    number, symbol = match.groups()
    aliases = {'ohm': 'Ω', 'ohms': 'Ω', 'Ω': 'Ω'}
    symbol = aliases.get(symbol, symbol)
    for label in ('ohms', 'ohm'):
        if symbol.endswith(label):
            symbol = symbol[:-len(label)] + 'Ω'
            break
    if unit == 'V/s':
        rate = re.fullmatch(r'([pnuµμmkMG]?)V/([pnuµμmkMG]?)s', symbol)
        if rate is None:
            invalid('Expected V/s with supported SI prefixes on voltage and time')
        exponent = _PREFIXES[rate[1]] - _PREFIXES[rate[2]]
    elif unit in {'%', 'ratio'}:
        if symbol != unit:
            invalid(f'Expected {unit} unit')
        exponent = 0
    else:
        if not symbol.endswith(unit) or symbol[:-len(unit)] not in _PREFIXES:
            invalid(f'Expected {unit} with a supported SI prefix')
        exponent = _PREFIXES[symbol[:-len(unit)]]
    try:
        value = Decimal(number).as_tuple()
        return Decimal((value.sign, value.digits, value.exponent + exponent))
    except InvalidOperation:
        invalid('Invalid numeric specification')


_NUMBER = r'([+-]?(?:\d+(?:\.\d+)?|\.\d+))'


def _quantity(raw: str, unit: str) -> Decimal | None:
    """Read one signed quantity with explicit units, without changing its text."""
    match = re.fullmatch(rf'\s*{_NUMBER}\s*([^\s]+)\s*', raw)
    if match is None:
        return None
    number, symbol = match.groups()
    if unit in {'°C', '%'}:
        return Decimal(number) if symbol == unit else None
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


def compare_intervals(left: tuple[Decimal, Decimal], right: tuple[Decimal, Decimal],
                      comparison: str) -> bool | None:
    """Compare a stored value interval against an entire requested interval.

    True confirms all values, False rules out the requirement, and None leaves
    overlap uncertain. The requested interval's limiting endpoint is fixed;
    uncertainty in the stored interval cannot justify choosing a favorable value.
    """
    if comparison == 'eq':
        if left == right:
            return True
        return False if left[1] < right[0] or left[0] > right[1] else None
    if comparison == 'gte':
        if left[0] >= right[1]:
            return True
        return False if left[1] < right[1] else None
    if comparison == 'lte':
        if left[1] <= right[0]:
            return True
        return False if left[0] > right[0] else None
    invalid('Unsupported interval comparison')


def contains_interval(available: tuple[Decimal, Decimal], requested: tuple[Decimal, Decimal]) -> bool:
    """Whether every requested point lies within an explicit available interval."""
    return available[0] <= requested[0] and requested[1] <= available[1]


def relative_interval_gap(left: tuple[Decimal, Decimal], right: tuple[Decimal, Decimal],
                          comparison: str) -> Decimal:
    """Dimensionless shortfall for ranking known value mismatches, not suitability."""
    with localcontext() as context:
        context.prec = 220
        if comparison == 'gte':
            gap = max(Decimal(0), right[1] - left[1])
        elif comparison == 'lte':
            gap = max(Decimal(0), left[0] - right[0])
        elif comparison == 'eq':
            gap = max(Decimal(0), right[0] - left[1], left[0] - right[1])
        else:
            invalid('Unsupported interval comparison')
        scale = max(abs(value) for value in (*left, *right))
        return gap / scale if scale else Decimal(0)
