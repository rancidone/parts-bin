"""
Ingestion pipeline: extraction → completeness → lookup → upsert.

`run_ingestion` is a generator that yields events for the SSE layer:
  {"type": "clarification", "message": str}   — missing fields, ask user
  {"type": "result",        "part": dict}      — committed part record
  {"type": "error",         "message": str}    — extraction failed hard
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from db.persistence import upsert
from ingestion.completeness import clarification_prompt, is_complete
from ingestion.lookup import fetch_specs, merge_specs
from llm.client import LLMClient


async def run_ingestion(
    db_path: str | Path,
    llm: LLMClient,
    user_message: str,
    image_b64: str | None = None,
    digikey_credentials: dict | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Run one ingestion turn. Yields a single event dict.

    - If extraction is incomplete, yields a clarification event.
      The caller (SSE handler) sends the clarification to the user and
      calls run_ingestion again with the user's reply.
    - If extraction is complete, performs lookup (for discrete_ic), upserts,
      and yields a result event.
    - On hard LLM failure, yields an error event.
    """
    try:
        record = await llm.extract(user_message, image_b64)
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    if not is_complete(record):
        yield {"type": "clarification", "message": clarification_prompt(record)}
        return

    # Spec lookup for new discrete/IC parts.
    if record.get("profile") == "discrete_ic" and record.get("part_number"):
        specs = await fetch_specs(record["part_number"], digikey_credentials)
        record = merge_specs(record, specs)

    part_id = upsert(db_path, record)
    yield {"type": "result", "part": {**record, "id": part_id}}
