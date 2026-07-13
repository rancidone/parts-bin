# Parts Bin

Electronics parts bin manager. Uses an LLM to add, remove, and search inventory via natural language or photos.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Node.js + npm
- An OpenAI API key
- A running [llama.cpp](https://github.com/ggerganov/llama.cpp) server if you want local inference later
- (Optional) Digikey API credentials for spec lookups

## Setup

```sh
# Install Python dependencies
uv sync

# Install UI dependencies
cd ui && npm install && cd ..
```

Copy [config.example.toml](/Users/maddie/repos/parts-bin/config.example.toml) to `config.toml` in the project root and fill in your OpenAI key:

```toml
[llm]
primary_backend = "openai"

[llama]
base_url = "http://localhost:8080"

[openai]
api_key = "your-api-key"
base_url = "https://api.openai.com/v1"
model = "gpt-5.6-luna"

[db]
path = "parts.db"

[digikey]
client_id = ""
client_secret = ""

[jlcparts]
db_path = ""
```

`gpt-5.6-luna` is the intended low-cost default. You can switch back to local later by changing `llm.primary_backend` to `"llama"` or using the UI toggle when both backends are configured.

## Start / Stop

```sh
./dev.sh          # start API (port 8000) and UI (port 5173)
./dev.sh stop     # stop both
```

- API: http://localhost:8000
- UI:  http://localhost:5173

## Docker

The repo now includes:

- `Dockerfile` — multi-stage build that compiles the Vite UI and serves it from FastAPI
- `compose.yaml` — simple app deployment with a mounted config file and persistent data directory

For container use, set your config paths to `/data` so the SQLite files survive restarts:

```toml
[db]
path = "/data/parts.db"

[jlcparts]
db_path = "/data/jlcparts.sqlite3"
```

Then start it with:

```sh
mkdir -p data
docker compose up --build -d
```

The app will be available at:

- `http://localhost:8000`

The container serves both the API and the built UI from the same port.
`config.toml` is mounted read-only from the repo root, and telemetry is written to `./data/telemetry.jsonl`.

## Telemetry

The server always writes compact JSONL LLM telemetry to `telemetry.jsonl` in the repo root.
Set `TELEMETRY_LOG_FILE` to override the path.

Each line is one event with:

- `ts`
- `telemetry_version`
- `event`
- `request_id`
- `operation`
- `backend`
- `model`
- `latency_ms`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `message_count`
- `image_count`
- `text_chars`
- `inventory_count`
- `response_chars`

## Tests

```sh
uv run pytest
```
