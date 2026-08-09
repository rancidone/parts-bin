# Local operations

Parts Bin supports three explicitly selected runtimes: `local`, `openai`, and
`codex`. A runtime is chosen when a thread is created and cannot change for
that thread. If a runtime is unavailable, the thread reports that runtime
failure; Parts Bin never falls back to an old or alternate runtime.

## Setup and capability checks

Start from a clean checkout:

```sh
uv sync
cd ui && npm install && cd ..
cp config.example.toml config.toml
mkdir -p data
uv run pytest agent_runtime evaluation test_log.py
./dev.sh
curl -fsS http://localhost:8000/health
```

Configure only the runtime you intend to use in `config.toml`.

- Local: point `agent.local.base_url` to an OpenAI-compatible server and set a
  model name. The model must reliably follow system instructions, handle the
  configured image modality when photos are used, and support native function
  tools. `supports_native_tools = false` is only for a local model that emits
  the exact documented Parts Bin JSON tool envelope; it is not a provider
  fallback or reduced compatibility mode.
- OpenAI: set `agent.openai.api_key` from a local secret-management mechanism,
  select a Responses API model, and leave `base_url` at the API endpoint unless
  using an authorized compatible endpoint. Do not commit `config.toml` or put
  the key in telemetry, shell history, screenshots, or scenario fixtures.
- Codex: authenticate the locally installed Codex CLI/app server using its
  normal interactive login before starting Parts Bin. The repository launcher
  then starts the app server and configures the Parts Bin MCP server. For the
  Docker deployment, set `CODEX_HOME` to the host Codex directory; Compose
  mounts only its `auth.json` into the container read-only. Parts Bin does not
  attempt programmatic login or a fallback if the app server is unavailable.

The health endpoint reports configuration availability, not provider login or
model-quality validation. Run a new thread with each configured runtime for an
end-to-end check. The optional smoke test is explicitly configured:

```sh
PARTS_BIN_SMOKE_RUNTIME=local uv run pytest e2e/test_agent_runtime_smoke.py
PARTS_BIN_SMOKE_RUNTIME=openai uv run pytest e2e/test_agent_runtime_smoke.py
PARTS_BIN_SMOKE_RUNTIME=codex uv run pytest e2e/test_agent_runtime_smoke.py
```

## Telemetry and diagnostics

`TELEMETRY_LOG_FILE` defaults to `telemetry.jsonl` (Docker uses
`/app/data/telemetry.jsonl`). It is JSONL with telemetry version 1. The fixed
event set is `agent_runtime_selected`, `agent_turn_finished`,
`agent_tool_started`, `agent_tool_finished`, `agent_tool_error`,
`agent_approval_decision`, `agent_loop_limit`, and `agent_runtime_error`.
Records contain runtime, opaque thread/argument fingerprints, tool name,
argument keys, latency, status, stable error code, approval boolean, and a
coarse domain outcome. They do not contain prompts, assistant text, image
payloads, credentials, tool values/results, or inventory records. The logging
guard redacts sensitive field names even if a caller accidentally supplies one.

Useful local diagnostics:

```sh
curl -fsS http://localhost:8000/health
tail -n 50 data/telemetry.jsonl
uv run pytest agent_runtime/test_telemetry.py evaluation/test_failures.py test_log.py
uv run python -m evaluation.runner --workspace /private/tmp/parts-bin-evals
sqlite3 data/parts.db 'PRAGMA integrity_check;'
```

Runtime failures use `agent_runtime_error`; rejected or invalid registry calls
use `agent_tool_error` with a `failure_scope` of `tool` or `domain`; an
exhausted bounded tool loop uses `agent_loop_limit`. This separates provider
configuration from tool/domain failures without retaining sensitive data.

## Backup and recovery

Back up both the inventory database and, when configured separately, the
conversation database. Use SQLite's online backup command while the service is
running so WAL state is included consistently:

```sh
mkdir -p backups
sqlite3 data/parts.db ".backup 'backups/parts-$(date +%Y%m%d-%H%M%S).db'"
sqlite3 backups/parts-YYYYMMDD-HHMMSS.db 'PRAGMA integrity_check;'
```

For recovery, stop Parts Bin, preserve the damaged file for investigation,
replace only the configured database with a verified backup, then start the
service and run `PRAGMA integrity_check`. Backups and telemetry are local data;
protect their filesystem permissions. SQLite files are supported for this
single-user local deployment only—network filesystems, multi-writer access,
and remote inventory synchronization are unsupported deployment modes.

## Evaluation-failure capture and promotion

Live evaluations remain opt-in (`PARTS_BIN_LIVE_EVAL=1`). When one fails,
capture a metadata-only artifact; it deliberately excludes prompts, images,
arguments, results, text, credentials, and inventory records:

```sh
uv run python -m evaluation.failures capture \
  --output .eval-artifacts/failure.json --runtime local \
  --failure-code tool_loop --scenario-id observed-local-loop
```

Review the failure, then write a new synthetic deterministic scenario JSON by
hand. Do not copy customer input or inventory into it. A named reviewer can
promote it into the Phase 05 suite:

```sh
uv run python -m evaluation.failures promote \
  --capture .eval-artifacts/failure.json \
  --candidate /path/to/reviewed-scenario.json \
  --approved-by "reviewer-name"
uv run pytest evaluation
```

Promotion appends only a new scenario ID, records the approver and timestamp
in the capture artifact, and refuses duplicate IDs or incomplete scenarios.
