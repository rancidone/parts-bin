"""Parts Bin HTTP adapter.

Conversation traffic has one path: the agent gateway and its normalized event
stream. Inventory and enrichment endpoints remain thin adapters over the
typed domain service.
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from collections.abc import AsyncGenerator
from pathlib import Path
from time import perf_counter

import log
from agent_runtime import (
    AgentGateway, ApprovalEngine, ApprovalResponse, CodexAppServerRuntime,
    CodexAppServerTransport, ConversationStore, ImageInput,
    LocalOpenAICompatibleRuntime, LocalOpenAICompatibleTransport,
    OpenAIResponsesRuntime, OpenAIResponsesTransport, PartsBinMCPClient,
)
from agent_runtime.telemetry import AgentTelemetry
from db.persistence import export_csv, init_db, list_all
from domain import (
    ApplyReviewRequest, DeletePartRequest, DomainError, FetchSpecsRequest,
    PartsBinService, ProvenanceRequest, RejectReviewRequest, UpdatePartRequest,
)
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from ingestion.lookup import fetch_specs_detailed


_CONFIG_PATH = Path(__file__).parent / "config.toml"
_UI_DIST_PATH = Path(__file__).parent / "ui" / "dist"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise RuntimeError(f"config.toml not found at {_CONFIG_PATH}")
    with open(_CONFIG_PATH, "rb") as config_file:
        return tomllib.load(config_file)


log.init()
_logger = log.get_logger("parts_bin.server")
_cfg = _load_config()
_DB_PATH = Path(_cfg["db"]["path"])
_agent_cfg = _cfg.get("agent", {})
_openai_cfg = _agent_cfg.get("openai", {})
_search_cfg = _cfg.get("search")
_DIGIKEY_CREDS: dict | None = (
    {"client_id": _cfg["digikey"]["client_id"], "client_secret": _cfg["digikey"]["client_secret"]}
    if _cfg.get("digikey", {}).get("client_id") else None
)
_JLCPARTS_DB_PATH = _cfg.get("jlcparts", {}).get("db_path") or None
_JLCPARTS_MIN_FREE_BYTES = int(_cfg.get("jlcparts", {}).get("min_free_bytes", 4 * 1024**3))
_JLCPARTS_MAX_SQLITE_BYTES = _cfg.get("jlcparts", {}).get("max_sqlite_bytes")
_jlcparts_dl_status = "idle"

app = FastAPI(title="Parts Bin")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
init_db(_DB_PATH)


def _domain_service() -> PartsBinService:
    async def fetcher(part_number: str) -> dict:
        return await fetch_specs_detailed(
            part_number, _DIGIKEY_CREDS, jlcparts_db_path=_JLCPARTS_DB_PATH,
            search_config=_search_cfg,
        )
    return PartsBinService(_DB_PATH, spec_fetcher=fetcher)


def _domain_error(exc: DomainError) -> HTTPException:
    status = 404 if exc.code.value in {"part_not_found", "review_not_found"} else 409 if exc.code.value in {"duplicate_part", "conflict", "ambiguous_target"} else 422
    return HTTPException(status_code=status, detail={"code": exc.code.value, "message": exc.message, "details": exc.details})


def _make_agent_runtime(runtime: str):
    from tools import PartsBinToolRegistry
    from tools.mcp_server import MCPServer

    registry = PartsBinToolRegistry(_domain_service())
    common = {"registry": registry, "store": _conversation_store, "approvals": _approval_engine, "telemetry": _agent_telemetry}
    if runtime == "openai":
        config = _openai_cfg
        if not config.get("api_key"):
            raise RuntimeError("OpenAI runtime is not configured (agent.openai.api_key)")
        return OpenAIResponsesRuntime(OpenAIResponsesTransport(
            api_key=config["api_key"], model=config.get("model", "gpt-5.6"),
            base_url=config.get("base_url", "https://api.openai.com/v1"),
        ), **common)
    if runtime == "local":
        config = _agent_cfg.get("local", {})
        if not config.get("base_url"):
            raise RuntimeError("Local runtime is not configured (agent.local.base_url)")
        return LocalOpenAICompatibleRuntime(LocalOpenAICompatibleTransport(
            model=config.get("model", "local"), base_url=config["base_url"],
            api_key=config.get("api_key") or None,
        ), supports_native_tools=bool(config.get("supports_native_tools", True)), **common)
    if runtime == "codex":
        transport = CodexAppServerTransport(command=_agent_cfg.get("codex", {}).get("command", ""))
        return CodexAppServerRuntime(transport, mcp_client=PartsBinMCPClient(MCPServer(registry)), **common)
    raise ValueError(f"Unknown runtime: {runtime}")


_conversation_store = ConversationStore(_agent_cfg.get("conversation_db_path", str(_DB_PATH)))
_approval_engine = ApprovalEngine()
_agent_telemetry = AgentTelemetry()
_agent_gateway = AgentGateway(_conversation_store, _make_agent_runtime, telemetry=_agent_telemetry)


@app.on_event("shutdown")
async def close_agent_gateway() -> None:
    await _agent_gateway.close()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _agent_sse(events) -> AsyncGenerator[str, None]:
    for event in events:
        yield _sse("agent_event", event.payload())


async def _agent_image(photo: UploadFile | None) -> ImageInput | None:
    if photo is None:
        return None
    if photo.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="Unsupported image type. Use JPEG, PNG, or WebP.")
    from photo.pipeline import MAX_UPLOAD_BYTES, preprocess
    raw = await photo.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 10 MB).")
    try:
        return ImageInput("image/jpeg", preprocess(raw))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/agent/threads")
async def create_agent_thread(body: dict) -> dict:
    runtime = body.get("runtime")
    if runtime not in {"codex", "openai", "local"}:
        raise HTTPException(status_code=422, detail="runtime must be codex, openai, or local")
    return {"thread_id": _agent_gateway.create_thread(runtime), "runtime": runtime}


@app.get("/agent/threads/{thread_id}/events")
async def resume_agent_thread(thread_id: str, after: int = 0) -> StreamingResponse:
    try:
        events = _agent_gateway.events(thread_id, after=max(after, 0))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown conversation thread") from exc
    return StreamingResponse(_agent_sse(events), media_type="text/event-stream")


@app.post("/agent/threads/{thread_id}/messages")
async def submit_agent_message(thread_id: str, message: str = Form(default=""), photo: UploadFile | None = File(default=None)) -> StreamingResponse:
    if not message.strip() and photo is None:
        raise HTTPException(status_code=422, detail="message or photo required")
    try:
        events = await _agent_gateway.submit(thread_id, message.strip() or "Identify this part.", image=await _agent_image(photo))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown conversation thread") from exc
    return StreamingResponse(_agent_sse(events), media_type="text/event-stream")


@app.post("/agent/threads/{thread_id}/approvals")
async def respond_to_agent_approval(thread_id: str, body: dict) -> StreamingResponse:
    request_id, approved = body.get("request_id"), body.get("approved")
    if not isinstance(request_id, str) or not isinstance(approved, bool):
        raise HTTPException(status_code=422, detail="request_id and approved boolean are required")
    try:
        events = await _agent_gateway.respond_to_approval(thread_id, ApprovalResponse(request_id, approved))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown conversation thread") from exc
    return StreamingResponse(_agent_sse(events), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "runtimes": {
        "codex": bool(_agent_cfg.get("codex", {}).get("command")),
        "openai": bool(_openai_cfg.get("api_key")),
        "local": bool(_agent_cfg.get("local", {}).get("base_url")),
    }}


@app.get("/inventory")
async def inventory() -> list[dict]:
    return [vars(part) for part in _domain_service().list()]


@app.get("/inventory/pending")
async def inventory_pending() -> dict:
    return {"reviews": _domain_service().list_pending_reviews()}


@app.get("/inventory/{part_id}/provenance")
async def inventory_part_provenance(part_id: int) -> dict:
    try:
        return {"part_id": part_id, "provenance": _domain_service().provenance(ProvenanceRequest(part_id))}
    except DomainError as exc:
        raise _domain_error(exc) from exc


@app.patch("/inventory/{part_id}")
async def update_inventory_part(part_id: int, body: dict) -> dict:
    fields = body.get("part")
    if not isinstance(fields, dict):
        raise HTTPException(status_code=422, detail="part object required")
    try:
        return {"part": vars(_domain_service().update_part(UpdatePartRequest(part_id, fields)))}
    except DomainError as exc:
        raise _domain_error(exc) from exc


@app.delete("/inventory/{part_id}")
async def delete_inventory_part(part_id: int) -> dict:
    try:
        _domain_service().delete_part(DeletePartRequest(part_id))
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return {"ok": True}


@app.get("/inventory/export.csv")
async def inventory_csv():
    return StreamingResponse(iter([export_csv(list_all(_DB_PATH))]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=inventory.csv"})


@app.post("/inventory/{part_id}/refresh")
async def refresh_part(part_id: int) -> dict:
    started = perf_counter()
    async def fetcher(part_number: str) -> dict:
        return await fetch_specs_detailed(part_number, _DIGIKEY_CREDS, jlcparts_db_path=_JLCPARTS_DB_PATH)
    try:
        result = await PartsBinService(_DB_PATH, spec_fetcher=fetcher).fetch_and_stage_specs(FetchSpecsRequest(part_id))
    except DomainError as exc:
        raise _domain_error(exc) from exc
    _logger.info("refresh proposed", extra={"part_id": part_id, "latency_ms": round((perf_counter() - started) * 1000, 1)})
    return {"part": vars(result["part"]), "proposed_updates": result["chosen_updates"], "provenance": result["durable_provenance"], "outcome": result["outcome"], "withheld_candidates": result.get("withheld_candidates", {})}


@app.post("/inventory/{part_id}/accept")
async def accept_refresh(part_id: int, body: dict) -> dict:
    updates, provenance = body.get("updates", {}), body.get("provenance", [])
    if not updates:
        raise HTTPException(status_code=422, detail="No updates to accept")
    try:
        return {"part": vars(_domain_service().apply_review(ApplyReviewRequest(part_id, updates, tuple(provenance))))}
    except DomainError as exc:
        raise _domain_error(exc) from exc


@app.post("/inventory/{part_id}/dismiss")
async def dismiss_review(part_id: int) -> dict:
    try:
        _domain_service().reject_review(RejectReviewRequest(part_id))
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return {"ok": True}


@app.get("/jlcparts/status")
async def jlcparts_status() -> dict:
    if not _JLCPARTS_DB_PATH:
        return {"status": "not_configured"}
    path = Path(_JLCPARTS_DB_PATH)
    if _jlcparts_dl_status in {"downloading", "error"}:
        return {"status": _jlcparts_dl_status, "path": str(path)}
    if path.exists():
        return {"status": "ready", "path": str(path), "size_mb": round(path.stat().st_size / 1_048_576, 1)}
    return {"status": "missing", "path": str(path)}


async def _run_jlcparts_download() -> None:
    global _jlcparts_dl_status
    _jlcparts_dl_status = "downloading"
    try:
        from ingestion.jlcparts_download import download_if_missing
        await download_if_missing(_JLCPARTS_DB_PATH, min_free_bytes=_JLCPARTS_MIN_FREE_BYTES, max_sqlite_bytes=_JLCPARTS_MAX_SQLITE_BYTES)
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


def _ui_index_path() -> Path:
    return _UI_DIST_PATH / "index.html"


def _resolve_ui_asset(relative_path: str) -> Path | None:
    candidate = (_UI_DIST_PATH / relative_path).resolve()
    try:
        candidate.relative_to(_UI_DIST_PATH.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


if (_UI_DIST_PATH / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_UI_DIST_PATH / "assets"), name="ui-assets")


@app.get("/", include_in_schema=False)
async def ui_root():
    if not _ui_index_path().is_file():
        raise HTTPException(status_code=404, detail="UI build not found")
    return FileResponse(_ui_index_path())


@app.get("/{full_path:path}", include_in_schema=False)
async def ui_catchall(full_path: str):
    if not full_path:
        return await ui_root()
    if full_path.startswith(("agent", "inventory", "health", "jlcparts")):
        raise HTTPException(status_code=404, detail="Not found")
    asset_path = _resolve_ui_asset(full_path)
    if asset_path is not None:
        return FileResponse(asset_path)
    if "." not in Path(full_path).name:
        return await ui_root()
    raise HTTPException(status_code=404, detail="Not found")
