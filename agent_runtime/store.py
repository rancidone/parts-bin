"""Durable, runtime-independent conversation metadata and visible events."""

from __future__ import annotations

from typing import Protocol

from .models import ConversationEvent, RuntimeName


class RuntimeSelectionError(ValueError):
    """Raised when code attempts to switch a thread to a different runtime."""


class UnsupportedRuntimeError(ValueError):
    """Historical conversations can be read but not executed by a new provider."""


class ConversationRepository(Protocol):
    """Conversation identity and ordered event storage, independent of its backend."""

    def create_thread(self, thread_id: str, runtime: RuntimeName) -> None: ...
    def runtime_for(self, thread_id: str) -> RuntimeName | None: ...
    def append(self, event: ConversationEvent) -> ConversationEvent: ...
    def append_once(self, event: ConversationEvent, event_id: str) -> ConversationEvent: ...
    def events(self, thread_id: str) -> list[ConversationEvent]: ...
