"""Parts Bin HTTP adapter.

Conversation traffic has one path: the agent gateway and its normalized event
stream. Inventory and enrichment endpoints remain thin adapters over the
typed domain service.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

import log
from agent_runtime import (
    ApprovalResponse, ImageInput, UnsupportedRuntimeError,
)
from application import ApplicationServices
from inventory_export import export_csv
from domain import (
    AdjustStockRequest, ApplyReviewRequest, DeletePartRequest, DomainError, FetchSpecsRequest,
    ProvenanceRequest, RejectReviewRequest, UpdatePartRequest,
)
from fastapi import APIRouter, Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles


_logger = log.get_logger("parts_bin.server")
router = APIRouter()


def _services(request: Request) -> ApplicationServices:
    return request.app.state.services


def create_app(services: ApplicationServices, *, ui_dist_path: Path | None = None) -> FastAPI:
    """Build an HTTP adapter from supplied application services."""
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await services.close()

    app = FastAPI(title="Parts Bin", lifespan=lifespan)
    app.state.services = services
    app.state.ui_dist_path = ui_dist_path
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    if ui_dist_path is not None and (ui_dist_path / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=ui_dist_path / "assets"), name="ui-assets")
    app.include_router(router)
    return app


def _domain_error(exc: DomainError) -> HTTPException:
    status = 404 if exc.code.value in {"part_not_found", "review_not_found"} else 409 if exc.code.value in {"duplicate_part", "conflict", "ambiguous_target"} else 422
    return HTTPException(status_code=status, detail={"code": exc.code.value, "message": exc.message, "details": exc.details})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _agent_sse(events) -> AsyncGenerator[str, None]:
    if hasattr(events, "__aiter__"):
        try:
            async for event in events:
                yield _sse("agent_event", event.payload())
        finally:
            await events.aclose()
    else:
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


@router.post("/agent/threads")
async def create_agent_thread(request: Request, body: dict | None = Body(default=None)) -> dict:
    if body:
        raise HTTPException(status_code=422, detail="Thread creation does not accept a runtime selection or other options")
    return {"thread_id": _services(request).gateway.create_thread()}


@router.get("/agent/threads")
async def list_agent_threads(request: Request) -> dict:
    return {"threads": [thread.payload() for thread in _services(request).gateway.threads()]}


@router.get("/agent/threads/{thread_id}/events")
async def resume_agent_thread(request: Request, thread_id: str, after: int = 0) -> StreamingResponse:
    try:
        events = _services(request).gateway.events(thread_id, after=max(after, 0))
    except UnsupportedRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown conversation thread") from exc
    return StreamingResponse(_agent_sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/agent/threads/{thread_id}/messages")
async def submit_agent_message(request: Request, thread_id: str, message: str = Form(default=""), photo: UploadFile | None = File(default=None)) -> StreamingResponse:
    if not message.strip() and photo is None:
        raise HTTPException(status_code=422, detail="message or photo required")
    try:
        events = _services(request).gateway.submit_stream(thread_id, message.strip() or "Identify this part.", image=await _agent_image(photo))
    except UnsupportedRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown conversation thread") from exc
    return StreamingResponse(_agent_sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/agent/threads/{thread_id}/resume")
async def resume_agent_execution(request: Request, thread_id: str, execution_id: str = Form(...),
                                 photo: UploadFile | None = File(default=None)) -> StreamingResponse:
    if not execution_id.strip():
        raise HTTPException(status_code=422, detail="execution_id is required")
    try:
        events = _services(request).gateway.resume_stream(thread_id, execution_id, image=await _agent_image(photo))
    except UnsupportedRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown conversation thread") from exc
    return StreamingResponse(_agent_sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/agent/threads/{thread_id}/approvals")
async def respond_to_agent_approval(request: Request, thread_id: str, body: dict) -> StreamingResponse:
    request_id, approved = body.get("request_id"), body.get("approved")
    if not isinstance(request_id, str) or not isinstance(approved, bool):
        raise HTTPException(status_code=422, detail="request_id and approved boolean are required")
    try:
        events = _services(request).gateway.approval_stream(thread_id, ApprovalResponse(request_id, approved))
    except UnsupportedRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown conversation thread") from exc
    return StreamingResponse(_agent_sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "agent_configured": _services(request).agent_configured}


@router.get("/inventory")
async def inventory(request: Request) -> list[dict]:
    return [vars(part) for part in _services(request).domain.list()]


@router.get("/inventory/pending")
async def inventory_pending(request: Request) -> dict:
    return {"reviews": _services(request).domain.list_pending_reviews()}


@router.get("/inventory/{part_id}/provenance")
async def inventory_part_provenance(request: Request, part_id: int) -> dict:
    try:
        return {"part_id": part_id, "provenance": _services(request).domain.provenance(ProvenanceRequest(part_id))}
    except DomainError as exc:
        raise _domain_error(exc) from exc


@router.patch("/inventory/{part_id}")
async def update_inventory_part(request: Request, part_id: int, body: dict) -> dict:
    fields = body.get("part")
    if not isinstance(fields, dict):
        raise HTTPException(status_code=422, detail="part object required")
    try:
        return {"part": vars(_services(request).domain.update_part(UpdatePartRequest(part_id, fields)))}
    except DomainError as exc:
        raise _domain_error(exc) from exc


@router.post("/inventory/{part_id}/quantity")
async def adjust_inventory_quantity(request: Request, part_id: int, body: dict) -> dict:
    if set(body) != {"delta"}:
        raise HTTPException(status_code=422, detail="delta is required and must be the only field")
    try:
        return {"part": vars(_services(request).domain.adjust_stock(AdjustStockRequest(part_id, body["delta"])))}
    except DomainError as exc:
        raise _domain_error(exc) from exc


@router.delete("/inventory/{part_id}")
async def delete_inventory_part(request: Request, part_id: int) -> dict:
    try:
        _services(request).domain.delete_part(DeletePartRequest(part_id))
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return {"ok": True}


@router.get("/inventory/export.csv")
async def inventory_csv(request: Request):
    return StreamingResponse(iter([export_csv([vars(part) for part in _services(request).domain.list()])]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=inventory.csv"})


@router.post("/inventory/{part_id}/refresh")
async def refresh_part(request: Request, part_id: int) -> dict:
    started = perf_counter()
    try:
        result = await _services(request).domain.fetch_and_stage_specs(FetchSpecsRequest(part_id))
    except DomainError as exc:
        raise _domain_error(exc) from exc
    _logger.info("refresh proposed", extra={"part_id": part_id, "latency_ms": round((perf_counter() - started) * 1000, 1)})
    return {"part": vars(result["part"]), "proposed_updates": result["chosen_updates"], "provenance": result["durable_provenance"], "outcome": result["outcome"], "withheld_candidates": result.get("withheld_candidates", {}), "lookup_candidates": result.get("lookup_candidates", []), "candidate_count": result.get("candidate_count", 0)}


@router.post("/inventory/{part_id}/accept")
async def accept_refresh(request: Request, part_id: int, body: dict) -> dict:
    updates, provenance = body.get("updates", {}), body.get("provenance", [])
    if not updates:
        raise HTTPException(status_code=422, detail="No updates to accept")
    try:
        return {"part": vars(_services(request).domain.apply_review(ApplyReviewRequest(part_id, updates, tuple(provenance))))}
    except DomainError as exc:
        raise _domain_error(exc) from exc


@router.post("/inventory/{part_id}/dismiss")
async def dismiss_review(request: Request, part_id: int) -> dict:
    try:
        _services(request).domain.reject_review(RejectReviewRequest(part_id))
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return {"ok": True}


def _ui_index_path(request: Request) -> Path | None:
    directory = request.app.state.ui_dist_path
    return None if directory is None else directory / "index.html"


def _resolve_ui_asset(request: Request, relative_path: str) -> Path | None:
    directory = request.app.state.ui_dist_path
    if directory is None:
        return None
    candidate = (directory / relative_path).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


@router.get("/", include_in_schema=False)
async def ui_root(request: Request):
    index = _ui_index_path(request)
    if index is None or not index.is_file():
        raise HTTPException(status_code=404, detail="UI build not found")
    return FileResponse(index)


@router.get("/{full_path:path}", include_in_schema=False)
async def ui_catchall(request: Request, full_path: str):
    if not full_path:
        return await ui_root(request)
    if full_path.startswith(("agent", "inventory", "health", "jlcparts")):
        raise HTTPException(status_code=404, detail="Not found")
    asset_path = _resolve_ui_asset(request, full_path)
    if asset_path is not None:
        return FileResponse(asset_path)
    if "." not in Path(full_path).name:
        return await ui_root(request)
    raise HTTPException(status_code=404, detail="Not found")
