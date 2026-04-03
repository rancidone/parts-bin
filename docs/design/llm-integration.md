# Design Unit: LLM Integration

## Problem
The server calls Qwen 3.5 via llama.cpp for two distinct tasks: structured extraction during ingestion (extract part fields from text/photo) and structured parsing during query (parse a natural language search into filter criteria). Both require reliable structured output from a local model.

## Interface
Use llama.cpp's OpenAI-compatible `/v1/chat/completions` endpoint. The server calls it over localhost HTTP. No special llama.cpp SDK — standard `httpx` async client.

## Structured Output
Use `response_format: {"type": "json_schema", "json_schema": {...}}` (llama.cpp grammar-backed JSON schema enforcement). This gives deterministic field presence without fragile prompt-only approaches.

If llama.cpp's JSON schema mode is unavailable at runtime, fall back to GBNF grammar via the `grammar` field.

### Ingestion Extraction Schema

```json
{
  "part_category": "string | null",
  "profile": "passive | discrete_ic | null",
  "value": "string | null",
  "package": "string | null",
  "part_number": "string | null"
  "quantity": "integer | null"
}
```

`profile` is the fixed two-value enum that drives all downstream logic — the LLM must emit it alongside `part_category`. Fields are `null` when the model cannot resolve them; null signals incompleteness to the completeness check (see Ingestion unit). No `location` or `notes` fields — neither is tracked.

### Query Parsing Schema

```json
{
  "filters": [{"field": "string", "op": "string", "value": "string"}],
  "freetext": "string | null"
}
```

Structured filter list plus remainder freetext for semantic matching.

## Prompt Templates
Two separate system prompts — ingestion and query have different extraction goals and output schemas.

**Ingestion system prompt** (summarized): "You are a parts inventory assistant. Extract part information from the user's message and/or photo. Classify the part as `passive` or `discrete_ic`. Return only valid JSON matching the schema. Set any field to null if it cannot be resolved."

**Query system prompt** (summarized): "You are a parts inventory search assistant. Parse the user's query into structured filter criteria. Return only valid JSON matching the schema."

System prompt is set per-call based on path (ingest vs. query). No shared session context between the two paths.

## Conversation History
- **Ingestion turns**: stateless per-call. Each ingestion is independent.
- **Query/chat turns**: the server maintains an in-memory list of `{role, content}` messages for the active session. The full history is replayed each call. History is cleared on page reload / new session.

## Context Window Management
llama.cpp does not perform automatic context compaction. The server is responsible for keeping the message history within the model's `n_ctx` limit.

**Strategy: hard turn cap with oldest-turn eviction.**

- Keep a `MAX_HISTORY_TURNS = 20` cap on the in-memory history list.
- When the list exceeds the cap, drop the oldest user+assistant turn pair (preserve the system prompt, which is prepended per-call and not stored in history).
- No token counting, no summarization. The cap is coarse but sufficient for parts bin query sessions; can be tuned at implementation time.

**Why not summarization?** A summarization step would require an additional LLM call, adding latency and complexity. Not worth it for this use case.

## Streaming
- **Chat/query path**: SSE tokens forwarded live from llama.cpp stream to client as they arrive.
- **Ingestion path**: response buffered until complete, then parsed as JSON. No streaming to client — client receives a single structured result event.

## Failure Handling
1. JSON parse fails after buffering → retry once with an appended user message: "Your previous response was not valid JSON. Return only the JSON object."
2. Second failure → return an error event to the client with the raw model output for debugging.
3. HTTP errors from llama.cpp → surface as server error to client immediately.

## What This Unit Does Not Cover
- Image preprocessing and base64 encoding (see Photo Pipeline)
- SSE event envelope format and HTTP routing (see Server/API)
- Database writes after extraction (see Ingestion unit)
