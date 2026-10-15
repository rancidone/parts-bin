"""Canonical nominal quantities: exact decimal base units, independent of labels."""

from dataclasses import dataclass
from decimal import Decimal
import re

from .quantities import numeric_value
from .errors import DomainError

BASE_UNITS = ('Ω', 'F', 'H', 'A', 'V', 'W', 'Hz', 's', 'm', 'cd', 'VA', '%', 'ratio', 'V/s')
CATEGORY_UNITS = {
    'resistor': 'Ω', 'potentiometer': 'Ω', 'resistance': 'Ω', 'capacitor': 'F',
    'capacitance': 'F', 'inductor': 'H', 'inductance': 'H', 'current': 'A',
    'amperage': 'A', 'voltage': 'V', 'power': 'W', 'frequency': 'Hz',
}
_PREFIX_EXPONENTS = {'r': 0, 'k': 3, 'm': 6, 'g': 9}
_PASSIVE_PREFIXES = {'p': -12, 'n': -9, 'u': -6, 'µ': -6, 'm': -3}


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def shifted(number: str, exponent: int) -> Decimal:
    value = Decimal(number).as_tuple()
    return Decimal((value.sign, value.digits, value.exponent + exponent))


@dataclass(frozen=True)
class NominalQuantity:
    decimal: str
    unit: str

    @property
    def display(self) -> str:
        value = Decimal(self.decimal)
        if self.unit == 'Ω':
            exponent = next((e for e in (9, 6, 3) if abs(value) >= Decimal(10) ** e), 0)
            suffix = {0: 'R', 3: 'k', 6: 'M', 9: 'G'}[exponent]
            text = decimal_text(shifted(self.decimal, -exponent))
            return text.replace('.', suffix) if '.' in text else text + suffix
        if self.unit in {'%', 'ratio', 'V/s'}:
            return f'{self.decimal} {self.unit}'
        exponent = 0 if value == 0 else max(-12, min(9, (abs(value).adjusted() // 3) * 3))
        prefix = {-12: 'p', -9: 'n', -6: 'µ', -3: 'm', 0: '', 3: 'k', 6: 'M', 9: 'G'}[exponent]
        return decimal_text(shifted(self.decimal, -exponent)) + prefix + self.unit


def parse_nominal(raw: str, unit: str | None = None) -> NominalQuantity | None:
    """Read unqualified scalars only; never infer limits, tolerances or suitability.

    Explicit SI prefixes retain case. Legacy bare resistor m means mega;
    explicit mΩ means milli. Lowercase omega repairs old persisted spellings.
    """
    if not isinstance(raw, str) or len(raw) > 100 or unit is not None and unit not in BASE_UNITS:
        return None
    text = raw.strip()
    if unit and re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)', text):
        return NominalQuantity(decimal_text(Decimal(text)), unit)
    # Multiplier-as-decimal labels have meaning only with a known dimension.
    eia = re.fullmatch(r'(\d+)([RrKkMmGgPpNnUuµμ])(\d+)', text)
    prefixes = _PREFIX_EXPONENTS if unit == 'Ω' else _PASSIVE_PREFIXES if unit in {'F', 'H'} else {}
    if eia and unit in {'F', 'H'} and eia[2] == 'M':
        return NominalQuantity(decimal_text(shifted(eia[1] + '.' + eia[3], 6)), unit)
    if eia and eia[2].lower().replace('μ', 'µ') in prefixes:
        value = shifted(eia[1] + '.' + eia[3], prefixes[eia[2].lower().replace('μ', 'µ')])
        return NominalQuantity(decimal_text(value), unit)
    if unit in {'Ω', 'F', 'H'}:
        legacy = re.fullmatch(r'(\d+(?:\.\d+)?|\.\d+)\s*([A-Za-zµμ]*)', text)
        if legacy:
            suffix = legacy[2].lower().replace('μ', 'µ')
            if unit == 'Ω':
                suffix = re.sub(r'ohms?$', '', suffix)
                # Unitless is handled above; r/k/m/g are historical resistor shorthand.
                if suffix in _PREFIX_EXPONENTS or suffix == '' and legacy[2]:
                    value = shifted(legacy[1], _PREFIX_EXPONENTS.get(suffix, 0))
                    return NominalQuantity(decimal_text(value), unit)
            else:
                # Keep SI M distinct from legacy lower-case m (milli).
                suffix = legacy[2].replace('μ', 'µ')
                suffix = suffix[:-1] if suffix.lower().endswith(unit.lower()) else suffix
                if suffix in _PASSIVE_PREFIXES:
                    return NominalQuantity(decimal_text(shifted(legacy[1], _PASSIVE_PREFIXES[suffix])), unit)
    if unit is None and re.fullmatch(r'\d+(?:\.\d+)?[Rr]', text):
        return NominalQuantity(decimal_text(Decimal(text[:-1])), 'Ω')
    text = text.replace('ω', 'Ω').replace('Ω', 'Ω')
    # Older stock lowercased unit symbols; restore unit case without lowercasing SI prefixes.
    explicit = re.fullmatch(r'([+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*)([pnuµμmkMG]?)([aAvVwWfFhH]|[hH][zZ])', text)
    if explicit:
        symbol = 'Hz' if explicit[3].lower() == 'hz' else explicit[3].upper()
        text = explicit[1] + explicit[2] + symbol
    signed = re.fullmatch(r'([+-]?)(.+)', text)
    if signed is None:
        return None
    sign, magnitude = signed.groups()
    for candidate in (unit,) if unit else BASE_UNITS:
        try:
            value = numeric_value(magnitude, candidate)
        except DomainError:
            continue
        if sign == '-':
            value = value.copy_negate()
        return NominalQuantity(decimal_text(value), candidate)
    return None


def nominal_for_category(raw: str, category: str, unit: str | None = None) -> NominalQuantity | None:
    return parse_nominal(raw, unit or CATEGORY_UNITS.get(category.strip().lower()))
