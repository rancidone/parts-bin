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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from db.persistence import export_csv, init_db, list_all
from ingestion.ingest import run_ingestion
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
# Routing heuristic
# ---------------------------------------------------------------------------

import re as _re

# Phrases that indicate an inventory mutation command.
# Anchored to avoid matching "do I have" or "how many ... I have".
_INGEST_PATTERNS = _re.compile(
    r"\b(add|remove|put|stock|got|bought|received)\b"
    r"|^i have\b",  # "I have X" at start of message only
    _re.IGNORECASE,
)


def _is_ingestion(message: str, has_photo: bool) -> bool:
    if has_photo:
        return True
    return bool(_INGEST_PATTERNS.search(message))


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _ingestion_stream(message: str, image_b64: str | None) -> AsyncGenerator[str, None]:
    async for evt in run_ingestion(_DB_PATH, _llm, message, image_b64, _DIGIKEY_CREDS):
        if evt["type"] == "result":
            yield _sse("result", {"type": "ingest", "part": evt["part"]})
        elif evt["type"] == "clarification":
            yield _sse("result", {"type": "clarification", "message": evt["message"]})
        elif evt["type"] == "error":
            yield _sse("error", {"message": evt["message"], "detail": ""})
    yield _sse("done", {})


async def _query_stream(message: str) -> AsyncGenerator[str, None]:
    result = await run_query(_DB_PATH, _llm, message, _history)
    if result["type"] == "results":
        yield _sse("result", {"type": "query", "matches": result["parts"]})
    elif result["type"] == "not_found":
        yield _sse("result", {"type": "query", "matches": [], "message": result["message"]})
    elif result["type"] == "error":
        yield _sse("error", {"message": result["message"], "detail": ""})
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
    message: str = Form(...),
    photo: UploadFile | None = File(default=None),
) -> StreamingResponse:
    # Validate photo type if provided.
    if photo is not None and photo.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Unsupported image type. Use JPEG, PNG, or WebP.")

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

    if _is_ingestion(message, has_photo=image_b64 is not None):
        stream = _ingestion_stream(message, image_b64)
    else:
        stream = _query_stream(message)

    return StreamingResponse(stream, media_type="text/event-stream")
