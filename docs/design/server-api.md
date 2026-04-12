---
status: draft
last_updated: 2026-04-12
---
# Design Unit: Server / API

## Problem
The web app needs a backend that receives chat messages and photo uploads, routes them to ingestion or query logic, calls the LLM, and returns results to the frontend with streaming support for perceived responsiveness on a local model.

## Framework
**FastAPI** (Python). Rationale: async-native, first-class multipart and SSE support, same ecosystem as Pillow and the LLM client. Single file (`server.py`) to start — no over-structuring for a single-user local app.

## Configuration

All runtime configuration lives in **`config.toml`** in the project root. The server reads it at startup and fails fast if required keys are missing.

```toml
[llama]
base_url = "http://localhost:8080"  # llama.cpp server endpoint
n_ctx = 4096                        # must match llama.cpp startup -c value

[db]
path = "parts.db"                   # SQLite file path, relative to project root

[digikey]
client_id = ""
client_secret = ""                  # leave empty to disable Digikey fallback
```

`digikey.client_id` / `client_secret` being empty disables the Digikey fallback cleanly — LCSC-only mode is the default.

## Endpoints

### `POST /chat`
The single interaction endpoint. Accepts multipart form data:
- `message: str` — user's text input (required)
- `photo: UploadFile` — optional photo attachment

**Routing logic** (server-side, not exposed to client):
- If a photo is attached → ingestion path
- Else if message looks like an inventory command ("add", "remove", "I have", "remove 3 of") → ingestion path
- Else → query/chat path

Returns: `text/event-stream` (SSE) regardless of path.

### `GET /inventory`
Returns full inventory as JSON array. Used by the UI to render the parts list. No pagination — single-user local app, inventory is small.

### `GET /health`
Returns `{"status": "ok"}`. Used for startup readiness check.

## SSE Event Envelope
All `/chat` responses are SSE streams. Event types:

```
event: token
data: {"text": "..."}

event: result
data: {"type": "ingest", "part": {...}}   # or {"type": "query", "matches": [...]}

event: error
data: {"message": "...", "detail": "..."}

event: done
data: {}
```

- **token**: streamed LLM output tokens (query/chat path only)
- **result**: final structured payload after extraction or query resolution
- **error**: LLM or server failure
- **done**: stream end sentinel

Ingestion path emits: `result` then `done` (no tokens — buffered).
Query path emits: `token`* then `result` then `done`.

## Photo Upload
Multipart form. Server reads `photo` bytes into memory, passes to photo pipeline preprocessing. No temp file written to disk.

## Error Contract
- 400: malformed request (missing `message`, unsupported file type)
- 500: LLM unreachable or unrecoverable extraction failure (also surfaced as SSE `error` event mid-stream if the stream has already started)
- Unrecoverable LLM failures mid-stream: emit `error` event, then `done`.

## CORS
`*` — single-user local app, frontend and backend on the same host. Not a security concern.

## What This Unit Does Not Cover
- LLM call mechanics (see LLM Integration)
- Image preprocessing (see Photo Pipeline)
- Ingestion business logic and DB writes (see Ingestion unit)
- Query resolution logic (see Query unit)