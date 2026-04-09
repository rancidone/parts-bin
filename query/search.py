"""
Query pipeline: LLM parse → normalize → DB lookup → result.

`run_query` returns a result dict:
  {"type": "results",     "parts": list[dict]}   — matched parts
  {"type": "not_found",   "message": str}         — definitive no-match
  {"type": "error",       "message": str}         — parse failure
"""

from pathlib import Path
from typing import Any

import log
from db.persistence import normalize_value, query
from llm.client import ConversationHistory, LLMClient

_logger = log.get_logger("parts_bin.query")

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
        parsed = await llm.parse_query(user_message)
    except ValueError as exc:
        _logger.error("query parse failed", extra={"error": str(exc)})
        return {"type": "error", "message": str(exc)}

    filters: list[dict] = parsed.get("filters") or []
    attrs = _filters_to_attrs(filters, part_category=None)
    _logger.info("query parsed", extra={"filters": filters, "attrs": attrs})

    parts = query(db_path, attrs)
    _logger.info("query result", extra={"match_count": len(parts), "attrs": attrs})

    answer = await llm.answer(user_message, parts, history)
    _logger.info("query answer", extra={"answer": answer})

    if parts:
        return {"type": "results", "parts": parts, "answer": answer}
    return {"type": "not_found", "answer": answer}
