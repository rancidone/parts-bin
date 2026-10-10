"""Durable, runtime-independent conversation metadata and visible events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
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


class ConversationStore:
    def __init__(self, database: str | Path):
        self.database = str(database)
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_threads (
                    thread_id TEXT PRIMARY KEY,
                    runtime TEXT NOT NULL CHECK(runtime IN ('codex', 'openai', 'local'))
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    thread_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    runtime TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY(thread_id, sequence),
                    FOREIGN KEY(thread_id) REFERENCES agent_threads(thread_id)
                );
            """)
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_event_keys (
                event_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, sequence INTEGER NOT NULL)""")

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def create_thread(self, thread_id: str, runtime: RuntimeName) -> None:
        with self._connection() as conn:
            row = conn.execute("SELECT runtime FROM agent_threads WHERE thread_id = ?", (thread_id,)).fetchone()
            if row is None:
                if runtime != "openai":
                    raise UnsupportedRuntimeError("Only OpenAI conversations can be created.")
                conn.execute("INSERT INTO agent_threads(thread_id, runtime) VALUES (?, ?)", (thread_id, runtime))
            elif row["runtime"] != runtime:
                raise RuntimeSelectionError(f"Thread {thread_id!r} is bound to {row['runtime']!r}, not {runtime!r}")

    def runtime_for(self, thread_id: str) -> RuntimeName | None:
        with self._connection() as conn:
            row = conn.execute("SELECT runtime FROM agent_threads WHERE thread_id = ?", (thread_id,)).fetchone()
        return None if row is None else row["runtime"]

    def append(self, event: ConversationEvent) -> ConversationEvent:
        return self._append(event, None)

    def append_once(self, event: ConversationEvent, event_id: str) -> ConversationEvent:
        return self._append(event, event_id)

    def _append(self, event: ConversationEvent, event_id: str | None) -> ConversationEvent:
        self.create_thread(event.thread_id, event.runtime)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event_id is not None:
                previous = conn.execute("""SELECT e.* FROM agent_events e JOIN agent_event_keys k
                    ON e.thread_id = k.thread_id AND e.sequence = k.sequence WHERE k.event_id = ?""", (event_id,)).fetchone()
                if previous is not None:
                    if previous["thread_id"] != event.thread_id:
                        raise ValueError("Event identity belongs to another conversation")
                    return ConversationEvent(previous["kind"], event.thread_id, previous["runtime"],
                                             json.loads(previous["data_json"]), previous["sequence"])
            next_sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_events WHERE thread_id = ?", (event.thread_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO agent_events(thread_id, sequence, kind, runtime, data_json) VALUES (?, ?, ?, ?, ?)",
                (event.thread_id, next_sequence, event.kind, event.runtime,
                 json.dumps(event.data, sort_keys=True, separators=(",", ":"))),
            )
            if event_id is not None:
                conn.execute("INSERT INTO agent_event_keys VALUES (?, ?, ?)", (event_id, event.thread_id, next_sequence))
        return ConversationEvent(event.kind, event.thread_id, event.runtime, event.data, next_sequence)

    def events(self, thread_id: str) -> list[ConversationEvent]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT sequence, kind, runtime, data_json FROM agent_events WHERE thread_id = ? ORDER BY sequence", (thread_id,)
            ).fetchall()
        return [ConversationEvent(row["kind"], thread_id, row["runtime"], json.loads(row["data_json"]), row["sequence"])
                for row in rows]
