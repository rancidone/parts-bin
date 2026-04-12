---
status: stable
last_updated: 2026-04-12
---
# Design Unit: Configuration

## Problem
Multiple subsystems — the LLM client, enrichment pipeline, database, and external API integrations — need runtime configuration. All of it should live in one place, readable at startup, with safe defaults where possible.

## Mechanism
`config.toml` in the project root. Read by the server at startup via `tomllib`; fails fast if required keys are missing. The file is gitignored since it contains API credentials.

## Schema

```toml
[llama]
base_url = "http://localhost:8080"    # llama.cpp server endpoint

[openai]
api_key = ""                          # leave empty to disable LLM fallback
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"

[db]
path = "parts.db"                     # SQLite file path, relative to project root

[digikey]
client_id = ""
client_secret = ""                    # leave empty to disable DigiKey enrichment

[jlcparts]
db_path = ""                          # leave empty to disable local catalog
min_free_bytes = 4294967296           # disk space required before extraction
max_sqlite_bytes = 21474836480        # reject extracted db above this size

[search]
# Presence of this section enables web search escalation (DuckDuckGo, no API key required).
# Omit the section entirely to disable.
```

## Opt-In Subsystems

- **OpenAI fallback**: disabled when `openai.api_key` is empty.
- **DigiKey enrichment**: disabled when `digikey.client_id` is empty.
- **JLC parts catalog**: disabled when `jlcparts.db_path` is empty.
- **Web search escalation**: disabled when `[search]` section is absent. Uses DuckDuckGo HTML search — no API key required. Used as last resort when all other enrichment stages fail.
