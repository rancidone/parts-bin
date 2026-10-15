"""Inventory identity normalization and historical passive slot repair."""

import re
from urllib.parse import urlsplit
from typing import Any

from .errors import DomainError, ErrorCode

from .nominal import nominal_for_category, CATEGORY_UNITS, BASE_UNITS


def normalize_value(raw: str, part_category: str) -> str:
    """Normalize recognized nominal values to exact decimal base-unit strings."""
    quantity = nominal_for_category(raw, part_category)
    return quantity.decimal if quantity else raw.strip().lower()


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
    for name in ("value", "package", "part_number", "manufacturer", "description", "datasheet_url", "value_unit"):
        if cleaned.get(name) is not None and not isinstance(cleaned[name], str):
            raise DomainError(ErrorCode.INVALID_INPUT, f"{name} must be a string or null")
    if cleaned.get('datasheet_url') is not None:
        try:
            url = urlsplit(cleaned['datasheet_url'])
            valid = (len(cleaned['datasheet_url']) <= 2048 and url.scheme == 'https'
                     and url.hostname and not url.username and not url.password
                     and not any(char.isspace() or ord(char) < 32 for char in cleaned['datasheet_url']))
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise DomainError(ErrorCode.INVALID_INPUT, 'datasheet_url must be an HTTPS URL without credentials, at most 2048 characters')
    if require_quantity and (not isinstance(cleaned.get("quantity"), int) or isinstance(cleaned.get("quantity"), bool) or cleaned["quantity"] < 0):
        raise DomainError(ErrorCode.INVALID_INPUT, "quantity must be a non-negative integer")
    unit = cleaned.get('value_unit')
    expected_unit = CATEGORY_UNITS.get(cleaned['part_category'].lower())
    if unit is not None and (unit not in BASE_UNITS or expected_unit and unit != expected_unit):
        raise DomainError(ErrorCode.INVALID_INPUT, 'Nominal value unit does not match its category')
    if unit and cleaned.get('value') is not None and nominal_for_category(cleaned['value'], cleaned['part_category'], unit) is None:
        raise DomainError(ErrorCode.INVALID_INPUT, 'Nominal value does not match its unit')
    return normalize_part_payload(cleaned)


def normalize_part_payload(part: dict) -> dict:
    payload = dict(part)
    payload.setdefault("manufacturer", None)
    payload = repair_fields(payload)
    if isinstance(payload.get("value"), str):
        quantity = nominal_for_category(payload['value'], payload['part_category'], payload.get('value_unit'))
        if quantity:
            payload['value'], payload['value_unit'] = quantity.decimal, quantity.unit
        elif payload.get('profile') == 'passive':
            payload['value'] = payload['value'].strip()
    elif payload.get('value') is None:
        payload['value_unit'] = None
    return payload


def part_identity(fields: dict) -> tuple | None:
    """Compare stock identities without treating unknown fields as wildcards."""
    fields = normalize_part_payload(fields)
    if fields.get("part_number") is not None:
        return ("part_number", fields["part_number"])
    if fields.get('part_category', '').lower() not in _PASSIVE_CATEGORIES:
        # A shared category/package or description cannot establish that
        # unnumbered devices, connectors, modules or kits are the same stock.
        return None
    return ("passive", fields.get("part_category"), fields.get("value"), fields.get("package"))
