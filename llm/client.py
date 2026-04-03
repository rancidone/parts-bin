"""
LLM client — Qwen 3.5 via llama.cpp OpenAI-compatible API.

Two call modes:
  extract()  — ingestion path; stateless, buffered, JSON schema output.
  stream()   — query/chat path; returns an async generator of token strings.

Conversation history management lives in ConversationHistory.
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

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
        },
        "required": ["part_category", "profile", "value", "package", "part_number", "quantity"],
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
    "or 'discrete_ic' (transistors, diodes, ICs, MOSFETs, etc.). "
    "Return only valid JSON matching the schema. "
    "Set any field to null if it cannot be resolved."
)

QUERY_SYSTEM_PROMPT = (
    "You are a parts inventory search assistant. "
    "Parse the user's query into structured filter criteria. "
    "Return only valid JSON matching the schema."
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
    ) -> dict[str, Any]:
        """
        Extract structured part data from a text message and optional image.

        Returns the parsed JSON dict.
        Raises ValueError if JSON parsing fails after one retry.
        Raises httpx.HTTPError on transport failures.
        """
        content = _build_content(user_message, image_b64)
        messages = [
            {"role": "system", "content": INGESTION_SYSTEM_PROMPT},
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
            return json.loads(raw)
        except json.JSONDecodeError:
            # One retry with an explicit correction nudge.
            messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "Your previous response was not valid JSON. Return only the JSON object.",
                },
            ]
            raw2 = await self._complete(messages, schema)
            try:
                return json.loads(raw2)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"LLM returned invalid JSON after retry. Raw output: {raw2!r}"
                ) from exc

    async def _complete(self, messages: list[dict], schema: dict[str, Any]) -> str:
        """Send a non-streaming chat completion request; return the content string."""
        payload = {
            "model": self._model,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": schema},
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._completions_url,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # Query / chat path — streaming, history-aware
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

        history.append("assistant", "".join(assembled))
