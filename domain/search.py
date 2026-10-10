"""Read-only nominal-value comparisons; these do not define stock identity."""

from decimal import Decimal
import re

from .normalization import normalize_value

_SCALES = {
    "resistor": {"r": 0, "k": 3, "m": 6, "g": 9},
    "capacitor": {"p": -12, "n": -9, "u": -6},
    "inductor": {"n": -9, "u": -6, "m": -3},
}
_NUMBER = r"([0-9]*\.?[0-9]+)"


def search_category(raw: str) -> str:
    """Compare known category synonyms without changing stored stock identity."""
    text = raw.strip().lower()
    spelling = re.sub(r"[\s_-]+", "", text)
    if spelling in {"opamp", "opamps", "operationalamplifier", "operationalamplifiers"}:
        return "operational amplifier"
    if spelling in {"bjt", "bjts", "bipolartransistor", "bipolartransistors", "bipolarjunctiontransistor", "bipolarjunctiontransistors"}:
        return "bjt"
    if spelling in {"mosfet", "mosfets"}:
        return "mosfet"
    if spelling in {"diode", "diodes"}:
        return "diode"
    if spelling in {"transistor", "transistors"}:
        return "transistor"
    return text


def nominal_value(raw: str, category: str) -> Decimal | None:
    """Return a base-unit value only for recognized category-specific notation.

    Legacy resistor 'm' means mega, as in inventory normalization. Explicit
    SI symbols retain case: mΩ is milli and MΩ is mega. Do not lowercase those.
    """
    category = category.lower()
    scales = _SCALES.get(category)
    if scales is None:
        return None
    text = raw.strip().replace("μ", "µ")
    symbol = re.fullmatch(_NUMBER + r"\s*([kKMmG]?)\s*[ΩΩ]", text) if category == "resistor" else None
    if symbol:
        number, prefix = symbol.groups()
        exponent = {"": 0, "k": 3, "K": 3, "M": 6, "m": -3, "G": 9}[prefix]
    else:
        text = re.sub(r"(?<=\d)\s+(?=[A-Za-zµ])", "", text)
        if category == "resistor":
            text = re.sub(r"ohms$", "ohm", text, flags=re.I)
        canonical = normalize_value(text, category).replace("µ", "u")
        match = re.fullmatch(_NUMBER + r"([a-z])", canonical)
        if match is None or match[2] not in scales:
            return None
        number, suffix = match.groups()
        exponent = scales[suffix]
    # Shift the decimal exponent exactly, independent of Decimal context precision.
    value = Decimal(number).as_tuple()
    return Decimal((value.sign, value.digits, value.exponent + exponent))


def values_match(stored: str, requested: str, category: str) -> bool:
    left, right = nominal_value(stored, category), nominal_value(requested, category)
    if left is not None or right is not None:
        return left is not None and right is not None and left == right
    return normalize_value(stored, category) == normalize_value(requested, category)
