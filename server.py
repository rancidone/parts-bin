"""
Parts Bin — FastAPI server.

Endpoints:
  POST /chat        multipart; returns SSE stream
  GET  /inventory   full inventory as JSON array
  GET  /health      readiness check
"""

import json
import tomllib
from collections.abc import AsyncGenerator
from pathlib import Path

import log
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from db.persistence import (
    export_csv,
    get_by_id,
    init_db,
    list_all,
    query,
    update_fields,
    update_fields_with_provenance,
    upsert,
)
from ingestion.lookup import fetch_specs, fetch_specs_detailed, merge_specs
from llm.client import ConversationHistory, LLMClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "config.toml"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise RuntimeError(f"config.toml not found at {_CONFIG_PATH}")
    with open(_CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


log.init()
_logger = log.get_logger("parts_bin.server")

_cfg = _load_config()
_DB_PATH = Path(_cfg["db"]["path"])
_DIGIKEY_CREDS: dict | None = (
    {"client_id": _cfg["digikey"]["client_id"], "client_secret": _cfg["digikey"]["client_secret"]}
    if _cfg["digikey"].get("client_id")
    else None
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Parts Bin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_llm = LLMClient(base_url=_cfg["llama"]["base_url"])
_history = ConversationHistory()

# Initialise DB at startup.
init_db(_DB_PATH)

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _execute_action(action_type: str, part: dict) -> tuple[int | None, str]:
    """Execute a db_action from the LLM. Returns (part_id, status)."""
    if action_type == "upsert":
        if not (part.get("part_category") and part.get("quantity")):
            return None, "invalid"
        part.setdefault("manufacturer", None)
        if part.get("part_number"):
            specs = await fetch_specs(part["part_number"], _DIGIKEY_CREDS)
            part = merge_specs(part, specs)
        return upsert(_DB_PATH, part), "saved"

    if action_type == "update":
        part_id = part.get("id")
        if not part_id:
            return None, "missing-target"
        return update_fields(_DB_PATH, part_id, part), "saved"

    if action_type == "lookup":
        part_id = part.get("id")
        part_number = part.get("part_number")
        if not part_id or not part_number:
            return None, "missing-target"
        lookup_result = await fetch_specs_detailed(part_number, _DIGIKEY_CREDS)
        chosen_updates = lookup_result["chosen_updates"]
        if chosen_updates:
            update_fields_with_provenance(
                _DB_PATH,
                part_id,
                chosen_updates,
                lookup_result["durable_provenance"],
            )
            _logger.info("lookup saved", extra={
                "part_id": part_id,
                "part_number": part_number,
                "provider": lookup_result["provider"],
                "tried_providers": lookup_result["tried_providers"],
                "fields": sorted(chosen_updates.keys()),
                "outcome": lookup_result["outcome"],
            })
            return part_id, "saved"
        _logger.info("lookup empty", extra={
            "part_id": part_id,
            "part_number": part_number,
            "provider": lookup_result["provider"],
            "tried_providers": lookup_result["tried_providers"],
            "lookup_status": lookup_result.get("status"),
            "conflicts": lookup_result.get("conflicts"),
        })
        if lookup_result.get("outcome") == "conflict":
            return part_id, "lookup-conflict"
        if lookup_result.get("status") == "timeout":
            return part_id, "lookup-timeout"
        return part_id, "no-specs"

    return None, "noop"


async def _chat_stream(message: str, image_b64: str | None) -> AsyncGenerator[str, None]:
    inventory = list_all(_DB_PATH)
    try:
        result = await _llm.chat(message, image_b64, _history, inventory)
    except Exception as exc:
        _logger.error("chat failed", extra={"error": str(exc)})
        yield _sse("error", {"message": str(exc), "detail": ""})
        yield _sse("done", {})
        return

    action = result["db_action"]
    action_type = action["type"]
    part = {k: action.get(k) for k in
            ("id", "part_category", "profile", "value", "package", "part_number", "quantity", "description")}

    part_id, action_status = await _execute_action(action_type, part)
    saved_part = get_by_id(_DB_PATH, part_id) if part_id and action_status == "saved" else None

    response_text = result["response"]
    if action_type == "lookup" and action_status == "saved":
        if saved_part:
            manufacturer = saved_part.get("manufacturer")
            details: list[str] = []
            if manufacturer:
                details.append(manufacturer)
            response_text = "I updated the inventory record with the fetched specifications."
            fetched_description = action.get("description")
            if fetched_description:
                details.append(fetched_description)
            if details:
                response_text += f"\n\n{'. '.join(details)}."
    elif action_type in ("update", "lookup") and action_status == "missing-target":
        response_text += "\n\n_(Note: the change wasn't saved — I couldn't identify which inventory record to update.)_"
    elif action_type == "lookup" and action_status == "lookup-timeout":
        response_text = "I reached the configured parts providers, but the DigiKey lookup timed out before it returned specifications."
    elif action_type == "lookup" and action_status == "lookup-conflict":
        response_text = "I found conflicting high-authority part metadata across the configured providers, so I did not update the inventory record automatically."
    elif action_type == "lookup" and action_status == "no-specs":
        response_text = "I ran the lookup, but the configured parts providers did not return matching specifications for that part number."

    _logger.info("chat", extra={"action": action_type, "action_status": action_status, "part_id": part_id,
                                "response": response_text})

    yield _sse("result", {
        "type": "chat",
        "response": response_text,
        "action": action_type,
        "part": saved_part,
    })
    yield _sse("done", {})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/inventory")
async def inventory() -> list[dict]:
    return list_all(_DB_PATH)


@app.get("/inventory/export.csv")
async def inventory_csv():
    rows = list_all(_DB_PATH)
    csv_str = export_csv(rows)
    return StreamingResponse(
        iter([csv_str]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inventory.csv"},
    )


@app.post("/chat")
async def chat(
    message: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
) -> StreamingResponse:
    # Validate photo type if provided.
    if photo is not None and photo.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Unsupported image type. Use JPEG, PNG, or WebP.")

    if not message and photo is None:
        raise HTTPException(status_code=422, detail="message or photo required")
    if not message and photo is not None:
        message = "add this"

    image_b64: str | None = None
    if photo is not None:
        from photo.pipeline import MAX_UPLOAD_BYTES, preprocess
        raw = await photo.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Image too large (max 10 MB).")
        try:
            image_b64 = preprocess(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    _logger.info("chat request", extra={"has_photo": image_b64 is not None, "user_message": message})
    return StreamingResponse(_chat_stream(message, image_b64), media_type="text/event-stream")
