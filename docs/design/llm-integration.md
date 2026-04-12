---
status: stable
last_updated: 2026-04-12
---
# Design Unit: LLM Integration

## Problem
The server needs reliable structured output from an LLM for all user interactions — ingestion, query, and chat. The primary model runs locally via llama.cpp; a cloud fallback is needed when the local server is unavailable.

## Interface
OpenAI-compatible `/v1/chat/completions`. The client is a plain `httpx` async wrapper — no SDK. This works against both llama.cpp and the OpenAI API without code changes.

**Primary**: llama.cpp at a configured base URL.
**Fallback**: OpenAI API, activated on `ConnectError` or `TimeoutException` from the primary. Disabled when `api_key` is empty in config.

## Primary Call Mode — `chat()`

All user interactions go through `chat()`. The LLM receives the full conversation history, current inventory, and the user message (plus optional image). It returns:

```json
{
  "response": "conversational reply to the user",
  "db_action": {
    "type": "upsert | update | lookup | delete | none",
    "...part fields and targeting..."
  }
}
```

The server executes `db_action` deterministically; the LLM never writes to the database directly.

Structured output is enforced via `response_format: {"type": "json_schema", "json_schema": {...}}`.

## Legacy Extraction Helpers

`parse_query()` and `answer()` are used by the `/query` endpoint. They use separate schemas and system prompts scoped to query parsing and freeform answer generation respectively. These paths are stateful via a separate `_query_history`.

## Conversation History

`ConversationHistory` maintains an in-memory list of `{role, content}` messages. Cap: 20 user+assistant turn pairs. Oldest pair is evicted when the cap is exceeded. The system prompt is prepended per-call and not stored in history. No token counting or summarization.

## Failure Handling

1. JSON parse fails after buffering → retry once with a correction nudge appended to the message list.
2. Second failure → raise `ValueError` with the raw output.
3. HTTP errors from the primary backend → fall back to OpenAI if configured, otherwise propagate.

## What This Unit Does Not Cover
- Image preprocessing (see `photo-pipeline.md`)
- SSE event format and HTTP routing (see `server-api.md`)
- Ingestion and query business logic (see their respective design docs)
