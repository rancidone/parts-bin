"""
Ingestion pipeline: extraction → completeness → lookup → upsert.

`run_ingestion` is a generator that yields events for the SSE layer:
  {"type": "clarification", "message": str}   — missing fields, ask user
  {"type": "result",        "part": dict}      — committed part record
  {"type": "error",         "message": str}    — extraction failed hard
"""

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import log
from db.persistence import query, update_fields, upsert
from ingestion.completeness import clarification_prompt, is_complete
from ingestion.lookup import fetch_specs, merge_specs
from llm.client import ConversationHistory, LLMClient

_logger = log.get_logger("parts_bin.ingestion")


async def run_ingestion(
    db_path: str | Path,
    llm: LLMClient,
    user_message: str,
    image_b64: str | None = None,
    digikey_credentials: dict | None = None,
    history: ConversationHistory | None = None,
    is_correction: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Run one ingestion turn. Yields a single event dict.

    history:       prior ingestion turns for LLM context; updated on success.
    is_correction: if True, update an existing matching part instead of upserting.
    """
    try:
        record = await llm.extract(
            user_message,
            image_b64,
            history_messages=history.messages() if history else None,
        )
    except ValueError as exc:
        _logger.error("ingestion extraction failed", extra={"error": str(exc)})
        yield {"type": "error", "message": str(exc)}
        return

    if is_correction:
        if not _is_identifiable(record):
            yield {"type": "clarification", "message": "I couldn't identify which part to update. Can you provide the part number or category, value, and package?"}
            return
    elif not is_complete(record):
        _logger.info("ingestion clarification needed", extra={"record": record})
        yield {"type": "clarification", "message": clarification_prompt(record)}
        return

    # manufacturer is not in the extraction schema; default it for the DB.
    record.setdefault("manufacturer", None)

    # Spec lookup for new discrete/IC parts.
    if record.get("profile") == "discrete_ic" and record.get("part_number"):
        specs = await fetch_specs(record["part_number"], digikey_credentials)
        record = merge_specs(record, specs)

    if is_correction:
        part_id = _apply_correction(db_path, record)
    else:
        part_id = None

    if part_id is None:
        part_id = upsert(db_path, record)

    # Store this exchange in history so follow-up corrections have context.
    if history is not None:
        history.append("user", user_message)
        history.append("assistant", json.dumps(record))

    _logger.info("ingestion complete", extra={"part_id": part_id, "is_correction": is_correction, "record": record})
    yield {"type": "result", "part": {**record, "id": part_id}}


def _is_identifiable(record: dict[str, Any]) -> bool:
    """Return True if the record has enough info to look up an existing part."""
    if record.get("part_number"):
        return True
    return bool(record.get("part_category") and record.get("value"))


def _apply_correction(db_path: str | Path, record: dict[str, Any]) -> int | None:
    """
    Find a unique existing part matching the record and update its fields.
    Returns part_id if a unique match was found and updated, else None.
    """
    if record.get("part_number"):
        matches = query(db_path, {"part_number": record["part_number"]})
    elif record.get("profile") == "passive":
        attrs = {k: record.get(k) for k in ("part_category", "value", "package") if record.get(k)}
        matches = query(db_path, attrs) if attrs else []
    else:
        return None

    if len(matches) != 1:
        return None

    return update_fields(db_path, matches[0]["id"], record)
