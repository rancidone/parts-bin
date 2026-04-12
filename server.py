"""
Parts Bin — FastAPI server.

Endpoints:
  POST /chat        multipart; returns SSE stream
  GET  /inventory   full inventory as JSON array
  GET  /health      readiness check
"""

import asyncio
import json
import re
import tomllib
from collections.abc import AsyncGenerator
from pathlib import Path
from time import perf_counter

import log
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from db.persistence import (
    clear_pending_review,
    delete_part,
    export_csv,
    get_by_id,
    init_db,
    list_all,
    list_field_provenance,
    list_pending_reviews,
    normalize_value,
    query,
    replace_part,
    save_pending_review,
    update_fields,
    update_fields_with_provenance,
    upsert,
)
from ingestion.lookup import fetch_specs_detailed
from llm.client import ConversationHistory, LLMClient
from query.search import run_query

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
_JLCPARTS_DB_PATH: str | None = _cfg.get("jlcparts", {}).get("db_path") or None
_JLCPARTS_MIN_FREE_BYTES: int = int(_cfg.get("jlcparts", {}).get("min_free_bytes", 4 * 1024 * 1024 * 1024))
_JLCPARTS_MAX_SQLITE_BYTES: int | None = _cfg.get("jlcparts", {}).get("max_sqlite_bytes")
_jlcparts_dl_status: str = "idle"  # idle | downloading | error
_background_enrichment_tasks: set[asyncio.Task] = set()
_PASSIVE_CATEGORIES = {"resistor", "capacitor", "inductor"}
_PACKAGE_TOKEN_RE = re.compile(
    r"^(?:\d{4}|\d{5}|"
    r"SOT-?\d+(?:-\d+)?|SOIC-?\d+|TSSOP-?\d+|MSOP-?\d+|SSOP-?\d+|"
    r"QFN-?\d+|DFN-?\d+|LQFP-?\d+|TQFP-?\d+|QFP-?\d+|DIP-?\d+|SOP-?\d+|TO-?\d+|"
    r"LED-SMD|panel-mount|through-hole|\d+(?:\.\d+)?mm)$",
    re.IGNORECASE,
)
_PASSIVE_VALUE_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:"
    r"R|K|M|G|OHM|OHMS|PF|NF|UF|µF|MH|UH|µH|NH|F|H"
    r")\s*$",
    re.IGNORECASE,
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

_openai_cfg = _cfg.get("openai", {})
_openai_key = _openai_cfg.get("api_key", "")
_llm = LLMClient(
    base_url=_cfg["llama"]["base_url"],
    fallback_url=_openai_cfg.get("base_url") if _openai_key else None,
    fallback_api_key=_openai_key or None,
    fallback_model=_openai_cfg.get("model") if _openai_key else None,
)
_history = ConversationHistory()
_query_history = ConversationHistory()

# Initialise DB at startup.
init_db(_DB_PATH)

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _track_background_task(task: asyncio.Task) -> None:
    _background_enrichment_tasks.add(task)
    task.add_done_callback(_background_enrichment_tasks.discard)


async def _enrich_upserted_part(part_id: int, part_number: str) -> None:
    try:
        lookup_result = await fetch_specs_detailed(
            part_number,
            _DIGIKEY_CREDS,
            jlcparts_db_path=_JLCPARTS_DB_PATH,
        )
        chosen_updates = lookup_result["chosen_updates"]
        if chosen_updates:
            save_pending_review(
                _DB_PATH,
                part_id,
                chosen_updates,
                lookup_result["durable_provenance"],
            )
            _logger.info("background upsert enrichment proposed", extra={
                "part_id": part_id,
                "part_number": part_number,
                "provider": lookup_result["provider"],
                "tried_providers": lookup_result["tried_providers"],
                "fields": sorted(chosen_updates.keys()),
                "outcome": lookup_result["outcome"],
            })
        else:
            _logger.info("background upsert enrichment empty", extra={
                "part_id": part_id,
                "part_number": part_number,
                "provider": lookup_result["provider"],
                "tried_providers": lookup_result["tried_providers"],
                "outcome": lookup_result["outcome"],
                "lookup_status": lookup_result.get("status"),
                "conflicts": lookup_result.get("conflicts"),
            })
    except Exception as exc:
        _logger.error("background upsert enrichment failed", extra={
            "part_id": part_id,
            "part_number": part_number,
            "error": str(exc),
        })


def _should_enqueue_enrichment(part: dict) -> bool:
    if not part.get("part_number"):
        return False
    if part.get("profile") != "discrete_ic":
        return False
    if str(part.get("part_category", "")).lower() in _PASSIVE_CATEGORIES:
        return False
    return True


def _coerce_part_payload(part: dict) -> dict:
    payload = dict(part)
    payload.setdefault("manufacturer", None)
    return _repair_part_payload(payload)


def _looks_like_package(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_PACKAGE_TOKEN_RE.match(value.strip()))


def _looks_like_passive_value(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    if not cleaned:
        return False
    if _PASSIVE_VALUE_RE.match(cleaned):
        return True
    return bool(re.match(r"^\d+[RrKkMmGg]\d+$", cleaned) or re.match(r"^\d+[PpNnUu]\d+$", cleaned))


def _repair_part_payload(part: dict) -> dict:
    repaired = dict(part)
    category = str(repaired.get("part_category") or "").lower()
    value = repaired.get("value")
    package = repaired.get("package")
    part_number = repaired.get("part_number")

    if category in _PASSIVE_CATEGORIES:
        repaired["profile"] = "passive"

        # Passive values belong in `value`; package tokens like 0402/0603 belong in `package`.
        if _looks_like_package(value) and _looks_like_passive_value(part_number):
            repaired["value"] = part_number
            repaired["part_number"] = None
        elif _looks_like_package(value) and not package:
            repaired["package"] = value
            repaired["value"] = None
        elif _looks_like_package(value) and _looks_like_passive_value(package):
            repaired["value"], repaired["package"] = package, value

        # Passives should not use the electrical value as `part_number` unless an explicit MPN exists.
        if _looks_like_passive_value(repaired.get("part_number")):
            if not _looks_like_passive_value(repaired.get("value")):
                repaired["value"] = repaired["part_number"]
            repaired["part_number"] = None

    return repaired


def _repair_action(action: dict) -> dict:
    repaired = dict(action)
    items = repaired.get("items")
    if isinstance(items, list):
        repaired["items"] = [_repair_part_payload(item) for item in items]
    elif repaired.get("type") == "upsert":
        repaired = _repair_part_payload(repaired)
    return repaired


def _merge_existing_part_for_replace(part_id: int, fields: dict) -> dict | None:
    existing = get_by_id(_DB_PATH, part_id)
    if existing is None:
        return None
    merged = {
        "part_category": fields.get("part_category", existing.get("part_category")),
        "profile": fields.get("profile", existing.get("profile")),
        "value": fields.get("value", existing.get("value")),
        "package": fields.get("package", existing.get("package")),
        "part_number": fields.get("part_number", existing.get("part_number")),
        "quantity": existing.get("quantity") if fields.get("quantity") is None else fields.get("quantity"),
        "manufacturer": fields.get("manufacturer", existing.get("manufacturer")),
        "description": fields.get("description", existing.get("description")),
    }
    return _repair_part_payload(merged)


def _execute_upsert_part(part: dict) -> int | None:
    if not (part.get("part_category") and part.get("quantity")):
        return None
    payload = _coerce_part_payload(part)
    part_id = upsert(_DB_PATH, payload)
    if _should_enqueue_enrichment(payload):
        _track_background_task(asyncio.create_task(_enrich_upserted_part(part_id, payload["part_number"])))
    return part_id


async def _execute_action(action: dict) -> tuple[int | None, str, dict]:
    """Execute a db_action from the LLM. Returns (part_id, status, extras)."""
    action = _repair_action(action)
    action_type = action["type"]
    if action_type == "upsert":
        items = action.get("items") or None
        if items:
            saved_ids: list[int] = []
            for item in items:
                part_id = _execute_upsert_part(item)
                if part_id is None:
                    return None, "invalid", {}
                saved_ids.append(part_id)
            return saved_ids[-1], "saved-batch", {"count": len(saved_ids)}

        part_id = _execute_upsert_part(action)
        if part_id is None:
            return None, "invalid", {}
        return part_id, "saved", {}

    if action_type == "update":
        # Filter+patch: deterministic DB lookup, then apply patch to all matching parts.
        filter_criteria = {k: v for k, v in (action.get("filter") or {}).items() if v is not None} or None
        patch = {k: v for k, v in (action.get("patch") or {}).items() if v is not None} or None
        if filter_criteria and patch:
            cat = filter_criteria.get("part_category")
            if "value" in filter_criteria and cat:
                filter_criteria["value"] = normalize_value(filter_criteria["value"], cat)
            matched = query(_DB_PATH, filter_criteria)
            if not matched:
                return None, "missing-target", {}
            patch_fields = patch
            saved_ids: list[int] = []
            for part in matched:
                part_id = part["id"]
                merged = _merge_existing_part_for_replace(part_id, patch_fields)
                if merged is None:
                    continue
                replace_part(_DB_PATH, part_id, merged)
                clear_pending_review(_DB_PATH, part_id)
                saved_ids.append(part_id)
            if not saved_ids:
                return None, "missing-target", {}
            return saved_ids[-1], "saved-batch", {
                "count": len(saved_ids),
                "fields": sorted(patch_fields.keys()),
            }

        items = action.get("items") or None
        if items:
            saved_ids: list[int] = []
            # Collect which fields actually varied across items.
            all_fields: set[str] = set()
            _part_field_keys = {"part_category", "profile", "value", "package", "part_number", "quantity", "description", "manufacturer"}
            for item in items:
                item_id = item.get("id")
                if not item_id:
                    return None, "missing-target", {}
                merged = _merge_existing_part_for_replace(item_id, item)
                if merged is None:
                    return None, "missing-target", {}
                replace_part(_DB_PATH, item_id, merged)
                clear_pending_review(_DB_PATH, item_id)
                saved_ids.append(item_id)
                all_fields.update(k for k in _part_field_keys if item.get(k) is not None)
            return saved_ids[-1], "saved-batch", {
                "count": len(saved_ids),
                "fields": sorted(all_fields),
            }

        part_id = action.get("id")
        if not part_id:
            return None, "missing-target", {}
        merged = _merge_existing_part_for_replace(part_id, action)
        if merged is None:
            return None, "missing-target", {}
        replace_part(_DB_PATH, part_id, merged)
        clear_pending_review(_DB_PATH, part_id)
        return part_id, "saved", {}

    if action_type == "lookup":
        part_id = action.get("id")
        part_number = action.get("part_number")
        if not part_id or not part_number:
            return None, "missing-target", {}
        lookup_result = await fetch_specs_detailed(part_number, _DIGIKEY_CREDS, jlcparts_db_path=_JLCPARTS_DB_PATH)
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
            return part_id, "saved", {}
        _logger.info("lookup empty", extra={
            "part_id": part_id,
            "part_number": part_number,
            "provider": lookup_result["provider"],
            "tried_providers": lookup_result["tried_providers"],
            "lookup_status": lookup_result.get("status"),
            "conflicts": lookup_result.get("conflicts"),
        })
        if lookup_result.get("outcome") == "conflict":
            return part_id, "lookup-conflict", {}
        if lookup_result.get("outcome") == "incomplete":
            return part_id, "lookup-incomplete", {}
        if lookup_result.get("outcome") == "failed":
            return part_id, "lookup-failed", {}
        if lookup_result.get("outcome") == "needs_confirmation":
            return part_id, "lookup-needs-confirmation", {}
        if lookup_result.get("status") == "timeout":
            return part_id, "lookup-timeout", {}
        return part_id, "no-specs", {}

    if action_type == "delete":
        part_id = action.get("id")
        if not part_id:
            return None, "missing-target", {}
        existing = get_by_id(_DB_PATH, part_id)
        if existing is None:
            return None, "missing-target", {}
        delete_part(_DB_PATH, part_id)
        return part_id, "deleted", {}

    return None, "noop", {}


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
    part_id, action_status, action_extras = await _execute_action(action)
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
    elif action_type == "upsert" and action_status == "saved-batch":
        response_text = result["response"]
    elif action_type == "delete" and action_status == "missing-target":
        response_text += "\n\n_(Note: the deletion wasn't applied — I couldn't identify which inventory record to remove.)_"
    elif action_type in ("update", "lookup") and action_status == "missing-target":
        response_text += "\n\n_(Note: the change wasn't saved — I couldn't identify which inventory record to update.)_"
    elif action_type == "lookup" and action_status == "lookup-timeout":
        response_text = "I reached the configured parts providers, but the DigiKey lookup timed out before it returned specifications."
    elif action_type == "lookup" and action_status == "lookup-conflict":
        response_text = "I found conflicting high-authority part metadata across the configured providers, so I did not update the inventory record automatically."
    elif action_type == "lookup" and action_status == "lookup-incomplete":
        response_text = "I found the part in the configured provider sources, but they still did not expose enough trustworthy metadata to update the inventory record."
    elif action_type == "lookup" and action_status == "lookup-failed":
        response_text = "The lookup terminated due to a provider or retrieval error before I could verify enough metadata to update the inventory record."
    elif action_type == "lookup" and action_status == "lookup-needs-confirmation":
        response_text = "I found a candidate source that still requires explicit confirmation before I can use it to update the inventory record."
    elif action_type == "lookup" and action_status == "no-specs":
        response_text = "I ran the lookup, but the configured parts providers did not return matching specifications for that part number."

    # Query filter: deterministic lookup when the LLM signals an inventory question.
    query_parts: list[dict] | None = None
    if action_type == "none":
        qf_attrs = {k: v for k, v in (action.get("query_filter") or {}).items() if v is not None}
        if qf_attrs:
            cat = qf_attrs.get("part_category")
            if "value" in qf_attrs and cat:
                qf_attrs["value"] = normalize_value(qf_attrs["value"], cat)
            query_parts = query(_DB_PATH, qf_attrs)
            _logger.info("chat query filter", extra={"attrs": qf_attrs, "match_count": len(query_parts)})

    _logger.info("chat", extra={"action": action_type, "action_status": action_status, "part_id": part_id,
                                "response": response_text})

    if query_parts is not None:
        yield _sse("result", {
            "type": "query",
            "response": response_text,
            "matches": query_parts,
        })
    else:
        payload: dict = {
            "type": "chat",
            "response": response_text,
            "action": action_type,
            "part": saved_part,
        }
        if action_status == "saved-batch" and action_extras:
            payload["batch_summary"] = action_extras
        yield _sse("result", payload)
    yield _sse("done", {})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/query")
async def query_inventory(body: dict) -> dict:
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message required")
    _logger.info("query request", extra={"user_message": message})
    result = await run_query(_DB_PATH, _llm, message, _query_history)
    return result


@app.get("/inventory")
async def inventory() -> list[dict]:
    return list_all(_DB_PATH)


@app.get("/inventory/pending")
async def inventory_pending() -> dict:
    return {"reviews": list_pending_reviews(_DB_PATH)}


@app.get("/inventory/{part_id}/provenance")
async def inventory_part_provenance(part_id: int) -> dict:
    part = get_by_id(_DB_PATH, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    return {"part_id": part_id, "provenance": list_field_provenance(_DB_PATH, part_id)}


@app.patch("/inventory/{part_id}")
async def update_inventory_part(part_id: int, body: dict) -> dict:
    part = get_by_id(_DB_PATH, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")

    fields = body.get("part")
    if not isinstance(fields, dict):
        raise HTTPException(status_code=422, detail="part object required")

    editable_fields = {
        "part_category", "profile", "value", "package", "part_number",
        "quantity", "manufacturer", "description",
    }
    cleaned = {k: v for k, v in fields.items() if k in editable_fields}
    if not cleaned:
        raise HTTPException(status_code=422, detail="No editable fields provided")

    merged = {
        key: cleaned.get(key, part.get(key))
        for key in editable_fields
    }

    for key in ("value", "package", "part_number", "manufacturer", "description"):
        if isinstance(merged.get(key), str):
            merged[key] = merged[key].strip() or None

    if not isinstance(merged.get("part_category"), str) or not merged["part_category"].strip():
        raise HTTPException(status_code=422, detail="part_category is required")
    merged["part_category"] = merged["part_category"].strip()

    if merged.get("profile") not in ("passive", "discrete_ic"):
        raise HTTPException(status_code=422, detail="profile must be 'passive' or 'discrete_ic'")

    if not isinstance(merged.get("quantity"), int) or merged["quantity"] < 0:
        raise HTTPException(status_code=422, detail="quantity must be a non-negative integer")

    merged = _repair_part_payload(merged)

    try:
        replace_part(_DB_PATH, part_id, merged)
    except Exception as exc:
        import sqlite3
        if isinstance(exc, sqlite3.IntegrityError):
            raise HTTPException(status_code=409, detail="Edit would conflict with an existing inventory record") from exc
        raise

    clear_pending_review(_DB_PATH, part_id)

    _logger.info("inventory part updated", extra={
        "part_id": part_id,
        "fields": sorted(cleaned.keys()),
    })
    return {"part": get_by_id(_DB_PATH, part_id)}


@app.delete("/inventory/{part_id}")
async def delete_inventory_part(part_id: int) -> dict:
    part = get_by_id(_DB_PATH, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")

    delete_part(_DB_PATH, part_id)
    _logger.info("inventory part deleted", extra={"part_id": part_id})
    return {"ok": True}


@app.get("/inventory/export.csv")
async def inventory_csv():
    rows = list_all(_DB_PATH)
    csv_str = export_csv(rows)
    return StreamingResponse(
        iter([csv_str]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inventory.csv"},
    )


@app.post("/inventory/{part_id}/refresh")
async def refresh_part(part_id: int) -> dict:
    """Fetch proposed spec updates without saving. Returns proposed_updates for user review."""
    refresh_started = perf_counter()
    part = get_by_id(_DB_PATH, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    part_number = part.get("part_number")
    if not part_number:
        raise HTTPException(status_code=422, detail="Part has no part number to look up")

    lookup_result = await fetch_specs_detailed(part_number, _DIGIKEY_CREDS, jlcparts_db_path=_JLCPARTS_DB_PATH)
    _logger.info("refresh proposed", extra={
        "part_id": part_id,
        "part_number": part_number,
        "fields": sorted(lookup_result["chosen_updates"].keys()),
        "outcome": lookup_result["outcome"],
        "lookup_stage_timings_ms": lookup_result.get("stage_timings_ms", {}),
        "refresh_handler_latency_ms": round((perf_counter() - refresh_started) * 1000, 1),
    })

    return {
        "part": part,
        "proposed_updates": lookup_result["chosen_updates"],
        "provenance": lookup_result["durable_provenance"],
        "outcome": lookup_result["outcome"],
        "withheld_candidates": lookup_result.get("withheld_candidates", {}),
    }


@app.post("/inventory/{part_id}/accept")
async def accept_refresh(part_id: int, body: dict) -> dict:
    """Commit user-accepted spec updates from a prior refresh."""
    updates = body.get("updates", {})
    provenance = body.get("provenance", [])
    if not updates:
        raise HTTPException(status_code=422, detail="No updates to accept")
    part = get_by_id(_DB_PATH, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    update_fields_with_provenance(_DB_PATH, part_id, updates, provenance)
    clear_pending_review(_DB_PATH, part_id, list(updates.keys()))
    _logger.info("refresh accepted", extra={"part_id": part_id, "fields": sorted(updates.keys())})
    return {"part": get_by_id(_DB_PATH, part_id)}


@app.post("/inventory/{part_id}/dismiss")
async def dismiss_review(part_id: int) -> dict:
    part = get_by_id(_DB_PATH, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    clear_pending_review(_DB_PATH, part_id)
    _logger.info("review dismissed", extra={"part_id": part_id})
    return {"ok": True}


@app.get("/jlcparts/status")
async def jlcparts_status() -> dict:
    if not _JLCPARTS_DB_PATH:
        return {"status": "not_configured"}
    db_path = Path(_JLCPARTS_DB_PATH)
    if _jlcparts_dl_status == "downloading":
        return {"status": "downloading", "path": str(db_path)}
    if _jlcparts_dl_status == "error":
        return {"status": "error", "path": str(db_path)}
    if db_path.exists():
        size_mb = round(db_path.stat().st_size / 1_048_576, 1)
        return {"status": "ready", "path": str(db_path), "size_mb": size_mb}
    return {"status": "missing", "path": str(db_path)}


async def _run_jlcparts_download() -> None:
    global _jlcparts_dl_status
    _jlcparts_dl_status = "downloading"
    try:
        from ingestion.jlcparts_download import download_if_missing
        await download_if_missing(
            _JLCPARTS_DB_PATH,
            min_free_bytes=_JLCPARTS_MIN_FREE_BYTES,
            max_sqlite_bytes=_JLCPARTS_MAX_SQLITE_BYTES,
        )
        _jlcparts_dl_status = "idle"
    except Exception as exc:
        _logger.error("jlcparts download failed", extra={"error": str(exc)})
        _jlcparts_dl_status = "error"


@app.post("/jlcparts/download")
async def jlcparts_download(background_tasks: BackgroundTasks) -> dict:
    global _jlcparts_dl_status
    if not _JLCPARTS_DB_PATH:
        raise HTTPException(status_code=422, detail="jlcparts.db_path not configured")
    if _jlcparts_dl_status == "downloading":
        return {"status": "already_downloading"}
    background_tasks.add_task(_run_jlcparts_download)
    _jlcparts_dl_status = "downloading"
    return {"status": "started"}


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
