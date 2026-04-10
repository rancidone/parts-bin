"""
Ingestion pipeline: extraction → completeness → write → enrichment proposal.

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
from db.persistence import get_by_id, query, save_pending_review, update_fields, upsert
from ingestion.completeness import clarification_prompt, is_complete
from ingestion.lookup import fetch_specs_detailed
from llm.client import ConversationHistory, LLMClient

_logger = log.get_logger("parts_bin.ingestion")


async def run_ingestion(
    db_path: str | Path,
    llm: LLMClient,
    user_message: str,
    image_b64: str | None = None,
    digikey_credentials: dict | None = None,
    jlcparts_db_path: str | None = None,
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

    if is_correction:
        part_id = _apply_correction(db_path, record)
    else:
        part_id = None

    if part_id is None:
        part_id = upsert(db_path, record)

    committed_part = get_by_id(db_path, part_id) or {**record, "id": part_id}

    enrichment_result: dict | None = None

    # Spec lookup for discrete/IC parts becomes a proposal flow after the write.
    if committed_part.get("profile") == "discrete_ic" and committed_part.get("part_number"):
        enrichment_result = await fetch_specs_detailed(
            committed_part["part_number"],
            digikey_credentials,
            jlcparts_db_path=jlcparts_db_path,
        )
        if enrichment_result["chosen_updates"]:
            save_pending_review(
                db_path,
                part_id,
                enrichment_result["chosen_updates"],
                enrichment_result["durable_provenance"],
            )

    # Store this exchange in history so follow-up corrections have context.
    if history is not None:
        history.append("user", user_message)
        history.append("assistant", json.dumps(committed_part))

    _logger.info("ingestion complete", extra={
        "part_id": part_id,
        "is_correction": is_correction,
        "record": committed_part,
        "enrichment_outcome": enrichment_result["outcome"] if enrichment_result else None,
    })
    yield {
        "type": "result",
        "part": committed_part,
        "enrichment": {
            "outcome": enrichment_result["outcome"],
            "conflicts": enrichment_result["conflicts"],
            "source_attempts": enrichment_result["source_attempts"],
            "proposed_updates": enrichment_result["chosen_updates"],
        } if enrichment_result else None,
    }


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
