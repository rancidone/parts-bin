"""Inventory identity normalization and historical passive slot repair."""

import re
from typing import Any

from .errors import DomainError, ErrorCode

# EIA multiplier-as-decimal: letter signals both multiplier and decimal point.
# e.g. 2R2 -> 2.2r, 4u7 -> 4.7u, 5K1 -> 5.1k, 1M5 -> 1.5m
_EIA_RE = re.compile(
    r"^(\d+)(R|K|M|G|P|N|U|H)(\d+)$",
    re.IGNORECASE,
)

# Suffix normalization maps for each domain.
# Keys are what may appear after stripping unit labels (ohm/f/h/etc).
_RESISTANCE_SUFFIXES = {
    "r": "r", "ohm": "r", "": "r",       # base unit
    "k": "k", "kohm": "k",
    "m": "m", "mohm": "m",               # mega
    "g": "g",
}
_CAPACITANCE_SUFFIXES = {
    "p": "p", "pf": "p",
    "n": "n", "nf": "n",
    "u": "u", "uf": "u", "µ": "u", "µf": "u",
}
_INDUCTANCE_SUFFIXES = {
    "n": "n", "nh": "n",
    "u": "u", "uh": "u", "µ": "u", "µh": "u",
    "m": "m", "mh": "m",               # milli
}

# EIA letter → canonical suffix per domain
_EIA_LETTER_MAP = {
    "resistance": {"r": "r", "k": "k", "m": "m", "g": "g"},
    "capacitance": {"p": "p", "n": "n", "u": "u"},
    "inductance":  {"n": "n", "u": "u", "m": "m"},
}

# Domain detection from part_category (lower-cased)
_CATEGORY_DOMAIN = {
    "resistor":  "resistance",
    "capacitor": "capacitance",
    "inductor":  "inductance",
}


def _domain_for_category(part_category: str) -> str | None:
    return _CATEGORY_DOMAIN.get(part_category.lower())


def _expand_eia(raw: str, domain: str) -> str | None:
    """
    Expand EIA multiplier-as-decimal notation into explicit decimal form.
    Returns expanded string (e.g. '2.2r') or None if not EIA format.
    """
    m = _EIA_RE.match(raw.strip())
    if not m:
        return None
    left, letter, right = m.group(1), m.group(2).lower(), m.group(3)
    letter_map = _EIA_LETTER_MAP.get(domain, {})
    canonical_suffix = letter_map.get(letter)
    if canonical_suffix is None:
        return None
    return f"{left}.{right}{canonical_suffix}"


def normalize_value(raw: str, part_category: str) -> str:
    """
    Normalize a raw value string to canonical form.

    Examples:
        normalize_value("10K",    "resistor")   -> "10k"
        normalize_value("2R2",    "resistor")   -> "2.2r"
        normalize_value("100nF",  "capacitor")  -> "100n"
        normalize_value("4u7",    "inductor")   -> "4.7u"
        normalize_value("0.1uF",  "capacitor")  -> "0.1u"
    """
    domain = _domain_for_category(part_category)
    if domain is None:
        # Unknown category — return lowercased as-is; no normalization possible.
        return raw.strip().lower()

    s = raw.strip().lower()

    # Try EIA multiplier-as-decimal first (before any other stripping).
    eia = _expand_eia(s, domain)
    if eia is not None:
        return eia

    # Only strip the unit appropriate to the category; incompatible units must
    # not become an invented rating (e.g. 10F is not a 10-ohm resistor).
    for unit_label in {"resistance": ("ohm",), "capacitance": ("f",), "inductance": ("h",)}[domain]:
        if s.endswith(unit_label):
            s = s[: -len(unit_label)]
            break

    # Split into numeric part and suffix.
    m = re.match(r"^([0-9]*\.?[0-9]+)([a-zµ]*)$", s)
    if not m:
        return raw.strip().lower()  # unrecognized format; pass through lowercased

    number, suffix = m.group(1), m.group(2)

    if domain == "resistance":
        canonical = _RESISTANCE_SUFFIXES.get(suffix)
        if canonical is None:
            return raw.strip().lower()
    elif domain == "capacitance":
        canonical = _CAPACITANCE_SUFFIXES.get(suffix)
        if canonical is None:
            return raw.strip().lower()
    elif domain == "inductance":
        canonical = _INDUCTANCE_SUFFIXES.get(suffix)
        if canonical is None:
            return raw.strip().lower()
    else:
        canonical = suffix

    return f"{number}{canonical}"


_PASSIVE_CATEGORIES = {"resistor", "capacitor", "inductor"}
_PACKAGE_TOKEN_RE = re.compile(r"^(?:\d{4}|\d{5}|SOT-?\d+(?:-\d+)?|SOIC-?\d+|TSSOP-?\d+|MSOP-?\d+|SSOP-?\d+|QFN-?\d+|DFN-?\d+|LQFP-?\d+|TQFP-?\d+|QFP-?\d+|DIP-?\d+|SOP-?\d+|TO-?\d+|LED-SMD|panel-mount|through-hole|\d+(?:\.\d+)?mm)$", re.I)
_PASSIVE_VALUE_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:R|K|M|G|OHM|OHMS|PF|NF|UF|µF|MH|UH|µH|NH|F|H)\s*$", re.I)


def _looks_like_package(value: Any) -> bool:
    return isinstance(value, str) and bool(_PACKAGE_TOKEN_RE.match(value.strip()))


def _looks_like_value(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return bool(_PASSIVE_VALUE_RE.match(value)) or bool(re.match(r"^\d+[RrKkMmGg]\d+$", value.strip())) or bool(re.match(r"^\d+[PpNnUu]\d+$", value.strip()))


def repair_fields(fields: dict) -> dict:
    result = dict(fields)
    category = str(result.get("part_category") or "").lower()
    if category not in _PASSIVE_CATEGORIES:
        return result
    result["profile"] = "passive"
    if _looks_like_package(result.get("value")) and _looks_like_value(result.get("part_number")):
        result["value"], result["part_number"] = result["part_number"], None
    elif _looks_like_package(result.get("value")) and not result.get("package"):
        result["package"], result["value"] = result["value"], None
    elif _looks_like_package(result.get("value")) and _looks_like_value(result.get("package")):
        result["value"], result["package"] = result["package"], result["value"]
    if _looks_like_value(result.get("part_number")):
        if not _looks_like_value(result.get("value")):
            result["value"] = result["part_number"]
        result["part_number"] = None
    return result


def clean_text(value: Any) -> Any:
    return value.strip() or None if isinstance(value, str) else value


def validate_fields(fields: dict, *, require_quantity: bool = True) -> dict:
    cleaned = {key: clean_text(value) for key, value in fields.items()}
    if not isinstance(cleaned.get("part_category"), str) or not cleaned["part_category"]:
        raise DomainError(ErrorCode.INVALID_INPUT, "part_category is required")
    if cleaned.get("profile") not in ("passive", "discrete_ic"):
        raise DomainError(ErrorCode.INVALID_INPUT, "profile must be 'passive' or 'discrete_ic'")
    for name in ("value", "package", "part_number", "manufacturer", "description"):
        if cleaned.get(name) is not None and not isinstance(cleaned[name], str):
            raise DomainError(ErrorCode.INVALID_INPUT, f"{name} must be a string or null")
    if require_quantity and (not isinstance(cleaned.get("quantity"), int) or isinstance(cleaned.get("quantity"), bool) or cleaned["quantity"] < 0):
        raise DomainError(ErrorCode.INVALID_INPUT, "quantity must be a non-negative integer")
    return normalize_part_payload(cleaned)


def normalize_part_payload(part: dict) -> dict:
    payload = dict(part)
    payload.setdefault("manufacturer", None)
    payload = repair_fields(payload)
    if payload.get("profile") == "passive" and isinstance(payload.get("value"), str):
        payload["value"] = normalize_value(payload["value"], payload["part_category"])
    return payload


def part_identity(fields: dict) -> tuple:
    """Compare stock identities without treating unknown fields as wildcards."""
    fields = normalize_part_payload(fields)
    if fields.get("part_number") is not None:
        return ("part_number", fields["part_number"])
    return ("passive", fields.get("part_category"), fields.get("value"), fields.get("package"))
