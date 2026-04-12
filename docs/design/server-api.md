---
status: stable
last_updated: 2026-04-12
---
# Design Unit: Server / API

## Problem
The web app needs a backend that receives chat messages and photo uploads, routes them to ingestion or query logic, calls the LLM, and returns results to the frontend. It also needs a REST surface for direct inventory management and enrichment review.

## Framework
**FastAPI** (Python). Async-native, first-class multipart and SSE support, same ecosystem as Pillow and the LLM client.

## Endpoints

### Chat

**`POST /chat`** — multipart form (`message: str`, `photo: UploadFile?`). Returns SSE stream.

The LLM receives the full message and current inventory, returns a conversational response plus a `db_action`. The server executes the action deterministically. No keyword routing — the LLM decides intent via `db_action.type`.

Action types: `upsert`, `update`, `lookup`, `delete`, `none`.

SSE events:

```
event: result
data: {"type": "chat", "response": "...", "action": "...", "part": {...}, "batch_summary": {...}}

event: result
data: {"type": "query", "response": "...", "matches": [...]}

event: error
data: {"message": "...", "detail": "..."}

event: done
data: {}
```

`/chat` does not stream tokens — the full response is buffered and emitted as a single `result` event.

**`POST /query`** — JSON body (`{"message": str}`). Non-streaming. Runs the query path directly (LLM parse → deterministic lookup → LLM answer). Returns `{"response": str, "matches": [...]}`.

### Inventory

| Method | Path | Description |
|---|---|---|
| `GET` | `/inventory` | Full inventory list |
| `PATCH` | `/inventory/{id}` | Update editable fields on a part |
| `DELETE` | `/inventory/{id}` | Delete a part |
| `GET` | `/inventory/export.csv` | CSV export of committed inventory |
| `GET` | `/inventory/pending` | All parts with pending review proposals |
| `GET` | `/inventory/{id}/provenance` | Accepted field provenance for a part |
| `POST` | `/inventory/{id}/refresh` | Fetch proposed spec updates from enrichment (returns proposals, does not commit) |
| `POST` | `/inventory/{id}/accept` | Commit user-accepted proposals from a prior refresh |
| `POST` | `/inventory/{id}/dismiss` | Clear pending review without committing |

### JLC Parts Catalog

| Method | Path | Description |
|---|---|---|
| `GET` | `/jlcparts/status` | Catalog status: `not_configured`, `missing`, `downloading`, `ready`, `error` |
| `POST` | `/jlcparts/download` | Trigger background catalog download |

### Health

`GET /health` — returns `{"status": "ok"}`.

## CORS

`*` — single-user local app, frontend and backend on the same host.

## What This Unit Does Not Cover
- LLM call mechanics and fallback (see `llm-integration.md`)
- Image preprocessing (see `photo-pipeline.md`)
- Ingestion and enrichment logic (see `ingestion-enrichment-integration.md`, `fallback-enrichment-pipeline.md`)
- Query resolution logic (see `query-runtime-alignment.md`)
