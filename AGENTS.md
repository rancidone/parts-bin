# Parts Bin agent guide

Parts Bin is an electronics-inventory application with a Python/FastAPI backend,
SQLite persistence, and a React/Vite UI. Follow the migration sequence in
[`TODO.md`](TODO.md); later phases must not be started before their listed
dependencies are complete.

Its core workflows are inventory management, natural-language or photo-based
ingestion, part-number identification and supplier-spec lookup, and inventory
search using stored parts and their specifications. Prefer simple, direct
implementations over speculative abstractions.

## Architecture direction

- The target is a local-first hybrid agent app with explicitly selected Codex,
  OpenAI API, and local runtimes.
- Keep inventory rules in a typed domain service. HTTP handlers, agent runtimes,
  MCP, and UI code are adapters, not alternate business-logic layers.
- Maintain one typed Parts Bin tool contract for every runtime. Models discover
  inventory through narrow tools; never inject the full inventory into prompts.
- Mutations require server-side validation and the shared approval policy. Do
  not add raw SQL tools, generic action/patch envelopes, direct model database
  writes, or compatibility/fallback paths.
- A conversation thread selects its runtime once and cannot silently switch.

## Repository map

- `server.py`: current FastAPI entry point and API adapters.
- `db/`: SQLite schema and persistence layer.
- `ingestion/`, `photo/`, `query/`: source lookup, image processing, and search.
- `llm/`: legacy orchestration scheduled for removal during the atomic cutover;
  do not extend it unless working explicitly on an earlier migration task.
- `ui/`: React/Vite client.
- `docs/todo/`: phase-specific implementation, test, and verification prompts.

## Local development

- Python requires 3.14+ and uses `uv`; the UI uses Node.js and npm.
- Copy `config.example.toml` to the untracked `config.toml` for local services.
  OpenAI and Digikey credentials are optional for the tests, but may be needed
  for live integrations.
- `./dev.sh` starts the API on port 8000 and Vite UI on port 5173. Docker serves
  the built UI and API together on port 8000.

## Working conventions

- Read the applicable phase document and relevant design docs before changing
  behavior. Keep changes scoped to that phase.
- Preserve existing SQLite data and business rules during extraction and
  cutover. Prefer explicit typed inputs, outputs, and stable domain errors.
- Do not commit credentials or local runtime configuration. Treat `config.toml`,
  databases, telemetry, and uploaded images as local data.
- Prefer focused tests alongside changed code. Run `uv run pytest` for backend
  changes; for UI changes run `npm run lint` and `npm run build` from `ui/`.
- Update docs and tests with behavior changes; delete obsolete paths rather than
  retaining deprecated shims once the cutover phase is reached.
