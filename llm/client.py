"""
LLM client — Qwen 3.5 via llama.cpp OpenAI-compatible API.

Primary call mode:
  chat()     — unified conversational path; returns response + optional DB action.

Legacy extraction helpers (used by ingestion/query pipelines in tests):
  extract()      — structured part extraction
  parse_query()  — query filter extraction
  answer()       — freeform answer given inventory context
"""

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

import log

_logger = log.get_logger("parts_bin.llm")

# ---------------------------------------------------------------------------
# Extraction schemas
# ---------------------------------------------------------------------------

INGESTION_SCHEMA: dict[str, Any] = {
    "name": "part_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "part_category": {"type": ["string", "null"]},
            "profile":       {"type": ["string", "null"], "enum": ["passive", "discrete_ic", None]},
            "value":         {"type": ["string", "null"]},
            "package":       {"type": ["string", "null"]},
            "part_number":   {"type": ["string", "null"]},
            "quantity":      {"type": ["integer", "null"]},
            "description":   {"type": ["string", "null"]},
        },
        "required": ["part_category", "profile", "value", "package", "part_number", "quantity", "description"],
        "additionalProperties": False,
    },
}

QUERY_SCHEMA: dict[str, Any] = {
    "name": "query_parse",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op":    {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["field", "op", "value"],
                    "additionalProperties": False,
                },
            },
            "freetext": {"type": ["string", "null"]},
        },
        "required": ["filters", "freetext"],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

INGESTION_SYSTEM_PROMPT = (
    "You are a parts inventory assistant. "
    "Extract part information from the user's message and/or photo. "
    "Classify the part as 'passive' (resistors, capacitors, inductors, etc.) "
    "or 'discrete_ic' (transistors, diodes, ICs, MOSFETs, LEDs, etc.). "
    "Populate 'description' with any useful details from the message such as color, "
    "polarity (common anode/cathode), wavelength, voltage rating, or other characteristics. "
    "Return only valid JSON matching the schema. "
    "Set any field to null if it cannot be resolved."
)

QUERY_SYSTEM_PROMPT = (
    "You are a parts inventory search assistant. "
    "Parse the user's query into structured filter criteria. "
    "Return only valid JSON matching the schema."
)

_PART_FIELDS: dict[str, Any] = {
    "part_category": {"type": ["string", "null"]},
    "profile":       {"type": ["string", "null"], "enum": ["passive", "discrete_ic", None]},
    "value":         {"type": ["string", "null"]},
    "package":       {"type": ["string", "null"]},
    "part_number":   {"type": ["string", "null"]},
    "quantity":      {"type": ["integer", "null"]},
    "description":   {"type": ["string", "null"]},
}

CHAT_SCHEMA: dict[str, Any] = {
    "name": "chat_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "response": {"type": "string"},
            "db_action": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["none", "upsert", "update", "lookup"]},
                    "id":   {"type": ["integer", "null"]},
                    **_PART_FIELDS,
                },
                "required": ["type", "id", *_PART_FIELDS.keys()],
                "additionalProperties": False,
            },
        },
        "required": ["response", "db_action"],
        "additionalProperties": False,
    },
}

CHAT_SYSTEM_PROMPT = (
    "You are a helpful electronics parts inventory assistant. "
    "You manage an inventory database and have natural conversations about parts.\n\n"
    "For every message return JSON with:\n"
    "  'response': your conversational reply to the user\n"
    "  'db_action.type': what to do with the database:\n"
    "    'upsert'  — user is adding parts or reporting stock (fill in part fields, quantity required)\n"
    "    'update'  — user is correcting or adding details to an existing part (set id from inventory, no quantity change)\n"
    "    'lookup'  — fetch specs from an external parts API for an existing part (set id and part_number)\n"
    "    'none'    — just chatting, answering a question, or you need more info before acting\n"
    "  For 'update': set db_action.id to the inventory id of the part being changed.\n"
    "  part fields in db_action: set to null when not applicable\n\n"
    "Use 'none' and ask naturally when you need more information. "
    "If inventory is provided below, use it to answer questions. "
    "Always respond conversationally — never output raw data at the user. "
    "In 'response', never interpolate field values directly — describe changes in plain prose only."
)

ANSWER_SYSTEM_PROMPT = (
    "You are a helpful electronics parts inventory assistant. "
    "Answer the user's question based on their inventory. "
    "Be concise and conversational. "
    "If inventory data is provided, use it to give specific, accurate answers. "
    "If the inventory is empty or doesn't contain what they asked about, say so."
)

# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_content(text: str, image_b64: str | None) -> list[dict] | str:
    """Build message content — plain string or multimodal list with image."""
    if image_b64 is None:
        return text
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
        },
    ]


MAX_HISTORY_TURNS = 20  # user+assistant pairs


class ConversationHistory:
    """In-memory conversation history for query/chat sessions."""

    def __init__(self, max_turns: int = MAX_HISTORY_TURNS) -> None:
        self._max_turns = max_turns
        # Each entry is {"role": ..., "content": ...}
        # Stored as pairs; we track [user_msg, assistant_msg, user_msg, ...]
        self._messages: list[dict[str, str]] = []

    def append(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})
        self._evict()

    def messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def _evict(self) -> None:
        # Count complete user+assistant turn pairs.
        # Drop the oldest pair when over cap.
        # A pair = two consecutive messages starting with role=user.
        while True:
            pairs = self._count_pairs()
            if pairs <= self._max_turns:
                break
            # Drop first user+assistant pair.
            self._messages = self._messages[2:]

    def _count_pairs(self) -> int:
        count = 0
        i = 0
        while i + 1 < len(self._messages):
            if self._messages[i]["role"] == "user" and self._messages[i + 1]["role"] == "assistant":
                count += 1
                i += 2
            else:
                i += 1
        return count


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Async client for llama.cpp's OpenAI-compatible chat completions endpoint.

    Args:
        base_url: llama.cpp server base URL (e.g. "http://localhost:8080").
        model:    Model name passed to the API (llama.cpp ignores it but it's required).
        timeout:  HTTP timeout in seconds for non-streaming calls.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        model: str = "qwen",
        timeout: float = 60.0,
    ) -> None:
        # Accept base URLs with or without a trailing /v1 path segment.
        stripped = base_url.rstrip("/")
        self._completions_url = (
            f"{stripped}/chat/completions"
            if stripped.endswith("/v1") or "/v1" in stripped.split("/")[-1:]
            else f"{stripped}/v1/chat/completions"
        )
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Ingestion extraction — stateless, buffered, JSON schema output
    # ------------------------------------------------------------------

    async def extract(
        self,
        user_message: str,
        image_b64: str | None = None,
        history_messages: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Extract structured part data from a text message and optional image.

        history_messages: prior ingestion turns (text only, no images) for context.
        Returns the parsed JSON dict.
        Raises ValueError if JSON parsing fails after one retry.
        Raises httpx.HTTPError on transport failures.
        """
        content = _build_content(user_message, image_b64)
        messages = [
            {"role": "system", "content": INGESTION_SYSTEM_PROMPT},
            *(history_messages or []),
            {"role": "user",   "content": content},
        ]
        return await self._extract_with_retry(messages, INGESTION_SCHEMA)

    async def _extract_with_retry(
        self,
        messages: list[dict],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        raw = await self._complete(messages, schema)
        try:
            result = json.loads(raw)
            _logger.debug("llm extract ok", extra={"schema": schema["name"], "result": result})
            return result
        except json.JSONDecodeError:
            _logger.warning("llm extract invalid json, retrying", extra={"schema": schema["name"], "raw": raw})
            # One retry with an explicit correction nudge.
            messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "Your previous response was not valid JSON. Return only the JSON object.",
                },
            ]
            raw2 = await self._complete(messages, schema, retry=True)
            try:
                result2 = json.loads(raw2)
                _logger.debug("llm extract retry ok", extra={"schema": schema["name"], "result": result2})
                return result2
            except json.JSONDecodeError as exc:
                _logger.error("llm extract failed after retry", extra={"schema": schema["name"], "raw": raw2})
                raise ValueError(
                    f"LLM returned invalid JSON after retry. Raw output: {raw2!r}"
                ) from exc

    async def _complete(self, messages: list[dict], schema: dict[str, Any], retry: bool = False) -> str:
        """Send a non-streaming chat completion request; return the content string."""
        payload = {
            "model": self._model,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": schema},
            "stream": False,
        }
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._completions_url,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        latency_ms = round((time.monotonic() - t0) * 1000)
        usage = data.get("usage", {})
        _logger.info(
            "llm complete",
            extra={
                "schema": schema["name"],
                "retry": retry,
                "latency_ms": latency_ms,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "messages": messages,
                "response": content,
            },
        )
        return content

    # ------------------------------------------------------------------
    # Query path — parse intent + conversational answer
    # ------------------------------------------------------------------

    async def parse_query(self, user_message: str) -> dict[str, Any]:
        """
        Parse a natural language query into structured filter criteria.

        Returns the parsed JSON dict (filters + freetext).
        Raises ValueError on JSON parse failure after retry.
        """
        messages = [
            {"role": "system", "content": QUERY_SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ]
        return await self._extract_with_retry(messages, QUERY_SCHEMA)

    async def answer(
        self,
        user_message: str,
        parts: list[dict],
        history: ConversationHistory,
    ) -> str:
        """
        Generate a conversational answer to the user's question given matching parts.

        Appends the exchange to history.
        """
        inventory_ctx = json.dumps(parts, indent=2) if parts else "No matching parts found."
        user_turn = f"{user_message}\n\nInventory context:\n{inventory_ctx}"

        history.append("user", user_message)
        messages = [
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            *history.messages()[:-1],  # history without the just-appended user turn
            {"role": "user", "content": user_turn},
        ]
        reply = await self._complete_text(messages)
        history.append("assistant", reply)
        return reply

    async def _complete_text(self, messages: list[dict]) -> str:
        """Send a non-streaming chat completion request with free-form text output."""
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._completions_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        latency_ms = round((time.monotonic() - t0) * 1000)
        usage = data.get("usage", {})
        _logger.info(
            "llm answer",
            extra={
                "latency_ms": latency_ms,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "messages": messages,
                "response": content,
            },
        )
        return content

    # ------------------------------------------------------------------
    # Unified conversational chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        user_message: str,
        image_b64: str | None,
        history: ConversationHistory,
        inventory: list[dict],
    ) -> dict[str, Any]:
        """
        Primary entry point for all user interactions.

        Returns {"response": str, "db_action": {"type": str, <part fields>}}.
        Updates history with the exchange.
        """
        content = _build_content(user_message, image_b64)

        system = CHAT_SYSTEM_PROMPT
        if inventory:
            system += f"\n\nCurrent inventory:\n{json.dumps(inventory, indent=2)}"

        messages = [
            {"role": "system", "content": system},
            *history.messages(),
            {"role": "user", "content": content},
        ]

        result = await self._extract_with_retry(messages, CHAT_SCHEMA)

        history.append("user", user_message)
        history.append("assistant", result["response"])
        return result

    # ------------------------------------------------------------------
    # Streaming (kept for future use)
    # ------------------------------------------------------------------

    async def stream(
        self,
        user_message: str,
        history: ConversationHistory,
    ) -> AsyncGenerator[str, None]:
        """
        Send a user message with conversation history; stream back token strings.

        Appends the user message to history before the call.
        Appends the fully-assembled assistant reply to history after streaming ends.

        Yields individual token strings as they arrive.
        """
        history.append("user", user_message)

        messages = [
            {"role": "system", "content": QUERY_SYSTEM_PROMPT},
            *history.messages(),
        ]
        payload = {
            "model": self._model,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": QUERY_SCHEMA},
            "stream": True,
        }

        assembled: list[str] = []
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                self._completions_url,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk["choices"][0].get("delta", {})
                    token = delta.get("content")
                    if token:
                        assembled.append(token)
                        yield token

        reply = "".join(assembled)
        latency_ms = round((time.monotonic() - t0) * 1000)
        _logger.info(
            "llm stream",
            extra={
                "latency_ms": latency_ms,
                "messages": messages,
                "response": reply,
            },
        )
        history.append("assistant", reply)
