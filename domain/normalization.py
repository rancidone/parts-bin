"""Inventory identity normalization and historical passive slot repair."""

import re
from typing import Any

from .errors import DomainError, ErrorCode

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
    if require_quantity and (not isinstance(cleaned.get("quantity"), int) or isinstance(cleaned.get("quantity"), bool) or cleaned["quantity"] < 0):
        raise DomainError(ErrorCode.INVALID_INPUT, "quantity must be a non-negative integer")
    return repair_fields(cleaned)


def normalize_part_payload(part: dict) -> dict:
    payload = dict(part)
    payload.setdefault("manufacturer", None)
    return repair_fields(payload)
