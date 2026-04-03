"""
Query pipeline: LLM parse → normalize → DB lookup → result.

`run_query` returns a result dict:
  {"type": "results",     "parts": list[dict]}   — matched parts
  {"type": "not_found",   "message": str}         — definitive no-match
  {"type": "error",       "message": str}         — parse failure
"""

from pathlib import Path
from typing import Any

from db.persistence import normalize_value, query
from llm.client import ConversationHistory, LLMClient

# Fields from the LLM filter list that map directly to DB columns.
_FILTERABLE_FIELDS = {"part_category", "profile", "value", "package", "part_number"}


def _filters_to_attrs(filters: list[dict], part_category: str | None) -> dict[str, Any]:
    """
    Convert the LLM filter list into a persistence.query() attrs dict.
    Only equality filters ("eq" or "=") on known fields are used.
    """
    attrs: dict[str, Any] = {}
    for f in filters:
        field = f.get("field")
        op = (f.get("op") or "").lower().strip()
        value = f.get("value")
        if field in _FILTERABLE_FIELDS and op in ("eq", "=", "==") and value:
            attrs[field] = value

    # Resolve part_category from attrs for normalization context.
    cat = attrs.get("part_category") or part_category
    if "value" in attrs and cat:
        attrs["value"] = normalize_value(attrs["value"], cat)

    return attrs


async def run_query(
    db_path: str | Path,
    llm: LLMClient,
    user_message: str,
    history: ConversationHistory,
) -> dict[str, Any]:
    """
    Parse a natural language query and execute a DB lookup.

    Returns a single result dict. History is updated by the LLM client.
    """
    try:
        parsed = await llm.extract(user_message)
    except ValueError as exc:
        return {"type": "error", "message": str(exc)}

    filters: list[dict] = parsed.get("filters") or []
    attrs = _filters_to_attrs(filters, part_category=None)

    parts = query(db_path, attrs)

    if parts:
        return {"type": "results", "parts": parts}
    return {"type": "not_found", "message": "That part is not in your inventory."}
