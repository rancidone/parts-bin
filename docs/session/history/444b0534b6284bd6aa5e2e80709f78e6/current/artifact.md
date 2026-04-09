# Parts Bin — Design Session

## Status
Design complete. All 7 units accepted at high readiness.

## Accepted Units

### Data Model & Persistence
SQLite, single `parts` table. Two profiles: `passive` (keyed on category+value+package) and `discrete_ic` (keyed on part_number). Partial unique indexes for duplicate detection. Persistence layer owns normalization and upsert. Normalization is a shared pure function used at both write and query time. `value` stored in canonical normalized form per the suffix table in the Query unit.

### Query
Natural language → LLM parsing → structured query record → normalize → exact-match DB lookup. Null fields are wildcards. No match = definitive not-in-inventory. Normalization suffix table defined here; shared with Ingestion.

### Ingestion
Photo/text → LLM extraction (part_category, profile, value, package, part_number, quantity — nulls for unresolved) → completeness check → clarification loop if incomplete → duplicate check → write. External spec lookup for new discrete/IC entries: LCSC primary (no auth), Digikey fallback (OAuth2 client credentials from config.toml). Lookup failure is non-fatal.

### Photo Pipeline
Vision path only — no OCR. Multipart upload → server-side Pillow resize (1024px longest side) + JPEG re-encode → base64 inline as image_url content part in chat completions message. Photos discarded after processing, never written to disk.

### LLM Integration
llama.cpp OpenAI-compatible `/v1/chat/completions` via httpx. Structured output via JSON schema mode (GBNF fallback). Ingestion extraction schema: `{part_category, profile, value, package, part_number, quantity}`. Query parsing schema: `{filters, freetext}`. Separate system prompts per path. Ingestion: stateless per-call, buffered. Query: in-memory history replay, SSE streamed live. Context window: hard cap of MAX_HISTORY_TURNS=20, oldest-turn eviction, no summarization. Failure: one retry on malformed JSON, then error event.

### Server / API
FastAPI. `POST /chat` multipart (message + optional photo) → SSE stream. Routing: photo present or command keywords → ingestion, else → query. SSE envelope: token / result / error / done. `GET /inventory`, `GET /health`. config.toml: `[llama]` base_url + n_ctx, `[db]` path, `[digikey]` client_id + client_secret (empty = LCSC-only). CORS wildcard.

### UI
React + TypeScript + Vite. No component library. Chat surface (default) + Inventory Browser surface via top-level nav toggle. SSE via EventSource, inventory via fetch. Chat: unified ingest+query, message thread, photo attach via file picker / camera. Inventory: client-side sort/filter, CSV export. BOM export from query result cards. Vite dev proxy to FastAPI during development. Camera requires HTTPS or localhost in production.

## Session Notes
- location and notes fields were considered and dropped — neither is tracked.
- Digikey requires OAuth2; LCSC does not. Empty Digikey credentials in config.toml disables fallback cleanly.
- llama.cpp has no automatic context compaction — server owns history truncation via turn cap.
- User is learning React for the first time; TypeScript retained at their preference.
