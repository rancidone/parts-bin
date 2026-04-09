# Parts Bin — Implementation

## What was built

Full implementation of all 6 design units in a single session.

### Backend (Python / uv)

| Module | Files | Notes |
|---|---|---|
| Data Model | `db/schema.sql`, `db/persistence.py` | SQLite, partial unique indexes, normalization |
| LLM Integration | `llm/client.py` | httpx async, JSON schema output, ConversationHistory with 20-turn eviction |
| Ingestion | `ingestion/completeness.py`, `ingestion/lookup.py`, `ingestion/ingest.py` | LCSC → Digikey fallback, completeness-gated clarification loop |
| Query | `query/search.py` | Filter→attrs with normalization, exact-match DB lookup |
| Photo Pipeline | `photo/pipeline.py` | Pillow resize to 1024px, JPEG @ 85, base64 |
| Server/API | `server.py`, `config.toml` | FastAPI, SSE envelope, routing heuristic, `/chat` + `/inventory` + `/health` |

### Frontend (React / TypeScript / Vite)

`ui/` — Chat surface (SSE via fetch stream), Inventory browser (sort/filter/CSV export), CSS modules, dark mode via `prefers-color-scheme`. Vite dev proxy to FastAPI on port 8000.

### Tests

- **74 unit tests** — all passing, covering pure logic (normalization, completeness, history eviction, photo preprocessing, routing heuristic, merge specs, CSV export)
- **14 E2E tests** in `e2e/` — skip automatically when `PARTS_BIN_LLM_BASE_URL` is unset or server unreachable; activate when server is online

## Key decisions made during implementation

- `LLMClient` accepts base URLs with or without trailing `/v1` — handles both `http://host:8080` and `http://host:8080/v1` forms
- Routing heuristic uses word-boundary regex to avoid "do I have" matching the "I have" ingest keyword
- `uv` for Python project management; `pytest-asyncio` in auto mode for E2E

## How to run

```bash
# Backend
uv run uvicorn server:app --reload

# Frontend
cd ui && npm run dev

# Unit tests
uv run pytest --ignore=e2e

# E2E tests (requires server)
export PARTS_BIN_LLM_BASE_URL=http://woody.brownfamily.house:8080/v1
export PARTS_BIN_LLM_MODEL=unsloth/Qwen3.5-9B-GGUF:Q4_K_M
uv run pytest e2e/ -v
```
