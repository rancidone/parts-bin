# Parts Bin

Parts Bin is a local-first electronics inventory app. Conversations run through
one agent gateway, a shared typed tool registry, and normalized durable events.
The inventory remains in a local SQLite database.

## Requirements

- Python 3.14+ and [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- At least one explicitly configured runtime: Codex, OpenAI API, or Local
- Optional DigiKey credentials for supplier spec lookup

## Setup

```sh
uv sync
cd ui && npm install && cd ..
cp config.example.toml config.toml
```

Configure only the runtimes you intend to use. The runtime is selected when a
conversation starts and remains fixed for that thread. An unavailable runtime
reports an error; it does not select another provider.

```toml
[agent]
conversation_db_path = "data/parts.db"

[agent.codex]
command = "codex exec"
model = "gpt-5.6-luna"

[agent.openai]
api_key = ""
base_url = "https://api.openai.com/v1"
model = "gpt-5.6"

[agent.local]
base_url = "http://localhost:8080/v1"
api_key = ""
model = "local"
supports_native_tools = true

[db]
path = "data/parts.db"
```

The Local runtime accepts an OpenAI-compatible inference endpoint. Set
`supports_native_tools = false` only for a model that follows the documented
strict JSON tool-call envelope. The repository Codex launcher starts
`codex app-server --stdio` and configures the repository's `tools.mcp_server`
as the `parts_bin` MCP server. Set `PARTS_BIN_CODEX_BIN` only when `codex` is
not on `PATH`.

See [local operations](docs/operations.md) for runtime capability requirements,
Codex authentication, OpenAI credential handling, telemetry, diagnostics,
backup/recovery, and redacted evaluation-failure promotion.

## Run locally

```sh
./dev.sh
```

The API runs on `http://localhost:8000`; Vite serves the development UI on
`http://localhost:5173`. Stop both with `./dev.sh stop`.

## Docker deployment

Keep `config.toml` and SQLite data outside the image. For a local-first
deployment, point the Local runtime at a reachable local inference service and
store the database under `data/`:

```toml
[agent]
conversation_db_path = "data/parts.db"

[db]
path = "data/parts.db"
```

```sh
mkdir -p data
docker compose up --build -d
```

The container serves the API and built UI at `http://localhost:8000`, mounts
`config.toml` read-only, and persists `data/`. Back up `data/parts.db` while
the service is stopped, or use SQLite's backup facility for an online backup.

## Tests

```sh
uv run pytest
cd ui && npm run lint && npm run build
```
