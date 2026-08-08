---
status: stable
last_updated: 2026-04-12
---
# Design Unit: Configuration

`config.toml` is read at startup with `tomllib`; it is gitignored because it
may contain provider credentials. Runtime configuration is explicit: a thread
can use only the runtime selected at creation.

## Schema

```toml
[agent]
conversation_db_path = "data/parts.db" # defaults to db.path when omitted

[agent.openai]
api_key = ""                          # leave empty to disable this runtime
base_url = "https://api.openai.com/v1"
model = "gpt-5.6"

[agent.local]
base_url = "http://localhost:8080/v1"
api_key = ""
model = "local"
supports_native_tools = true

[agent.codex]
command = "python -m tools.codex_app_server" # repo launcher for Codex app-server + Parts Bin MCP

[db]
path = "data/parts.db"                # SQLite file path, relative to project root

[digikey]
client_id = ""
client_secret = ""                    # leave empty to disable DigiKey enrichment

[jlcparts]
db_path = "data/jlcparts.sqlite3"     # leave empty to disable local catalog
min_free_bytes = 4294967296           # disk space required before extraction
max_sqlite_bytes = 21474836480        # reject extracted db above this size

[search]
# Presence of this section enables web search escalation (DuckDuckGo, no API key required).
# Omit the section entirely to disable.
```

## Opt-In Subsystems

- **OpenAI runtime**: unavailable when `agent.openai.api_key` is empty.
- **Local runtime**: unavailable when `agent.local.base_url` is empty or omitted.
- **Codex runtime**: unavailable when `agent.codex.command` is empty or omitted.
- **DigiKey enrichment**: disabled when `digikey.client_id` is empty.
- **JLC parts catalog**: disabled when `jlcparts.db_path` is empty.
- **Web search escalation**: disabled when `[search]` section is absent. Uses DuckDuckGo HTML search — no API key required. Used as last resort when all other enrichment stages fail.
