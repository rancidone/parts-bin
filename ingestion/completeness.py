"""
Completeness check for extracted part records.

Required fields depend on profile. This module is a pure function —
no I/O, no LLM calls.
"""

from typing import Any

# Required fields by profile.
_REQUIRED: dict[str, list[str]] = {
    "passive":     ["part_category", "profile", "value", "package", "quantity"],
    "discrete_ic": ["part_category", "profile", "part_number", "quantity"],
}

# Fields required regardless of profile (needed to determine which set applies).
_ALWAYS_REQUIRED = ["part_category", "profile"]


def missing_fields(record: dict[str, Any]) -> list[str]:
    """
    Return the list of required fields that are null or absent.

    If `profile` itself is missing, returns ["profile"] — can't determine
    the rest of the required set without it.
    """
    # Check always-required first.
    for field in _ALWAYS_REQUIRED:
        if not record.get(field):
            return [field]

    profile = record["profile"]
    required = _REQUIRED.get(profile)
    if required is None:
        # Unknown profile — treat everything as missing.
        return [f for f in _ALWAYS_REQUIRED if not record.get(f)] + ["profile (unrecognized)"]

    return [f for f in required if not record.get(f)]


def is_complete(record: dict[str, Any]) -> bool:
    return len(missing_fields(record)) == 0


def clarification_prompt(record: dict[str, Any]) -> str:
    """
    Build a plain-language prompt naming the missing fields.
    """
    missing = missing_fields(record)
    present = {k: v for k, v in record.items() if v is not None}

    lines = ["I need a bit more information to add this part."]
    if present:
        summary = ", ".join(f"{k}={v}" for k, v in present.items() if k not in ("manufacturer", "description"))
        lines.append(f"So far I have: {summary}.")
    lines.append(f"Could you provide: {', '.join(missing)}?")
    return " ".join(lines)
