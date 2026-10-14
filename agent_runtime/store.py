"""Durable, runtime-independent conversation metadata and visible events."""

from __future__ import annotations

from typing import Protocol

from .models import ConversationEvent, ConversationSummary, RuntimeName


class RuntimeSelectionError(ValueError):
    """Raised when code attempts to switch a thread to a different runtime."""


class UnsupportedRuntimeError(ValueError):
    """Historical conversations can be read but not executed by a new provider."""


class ConversationRepository(Protocol):
    """Conversation identity and ordered event storage, independent of its backend."""

    @property
    def storage_id(self) -> str: ...

    def create_thread(self, thread_id: str, runtime: RuntimeName) -> None: ...
    def runtime_for(self, thread_id: str) -> RuntimeName | None: ...
    def threads(self) -> list[ConversationSummary]: ...
    def append(self, event: ConversationEvent) -> ConversationEvent: ...
    def append_once(self, event: ConversationEvent, event_id: str) -> ConversationEvent: ...
    def events(self, thread_id: str, *, after: int = 0) -> list[ConversationEvent]: ...


import json

def conversation_title(rows) -> str:
    fallback = "Empty conversation"
    for preferred_kind in ("user_message", "assistant_text"):
        for row in rows:
            if row["kind"] != preferred_kind:
                continue
            data = json.loads(row["data_json"])
            text = " ".join(str(data.get("text", "")).split())
            if text:
                return text[:61] + "…" if len(text) > 62 else text
            if preferred_kind == "user_message" and data.get("image"):
                fallback = "Photo request"
    return fallback
