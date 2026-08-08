"""Durable, runtime-independent conversation metadata and visible events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import ConversationEvent, RuntimeName


class RuntimeSelectionError(ValueError):
    """Raised when code attempts to switch a thread to a different runtime."""


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

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def create_thread(self, thread_id: str, runtime: RuntimeName) -> None:
        with self._connection() as conn:
            row = conn.execute("SELECT runtime FROM agent_threads WHERE thread_id = ?", (thread_id,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO agent_threads(thread_id, runtime) VALUES (?, ?)", (thread_id, runtime))
            elif row["runtime"] != runtime:
                raise RuntimeSelectionError(f"Thread {thread_id!r} is bound to {row['runtime']!r}, not {runtime!r}")

    def runtime_for(self, thread_id: str) -> RuntimeName | None:
        with self._connection() as conn:
            row = conn.execute("SELECT runtime FROM agent_threads WHERE thread_id = ?", (thread_id,)).fetchone()
        return None if row is None else row["runtime"]

    def append(self, event: ConversationEvent) -> ConversationEvent:
        self.create_thread(event.thread_id, event.runtime)
        with self._connection() as conn:
            next_sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_events WHERE thread_id = ?", (event.thread_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO agent_events(thread_id, sequence, kind, runtime, data_json) VALUES (?, ?, ?, ?, ?)",
                (event.thread_id, next_sequence, event.kind, event.runtime,
                 json.dumps(event.data, sort_keys=True, separators=(",", ":"))),
            )
        return ConversationEvent(event.kind, event.thread_id, event.runtime, event.data, next_sequence)

    def events(self, thread_id: str) -> list[ConversationEvent]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT sequence, kind, runtime, data_json FROM agent_events WHERE thread_id = ? ORDER BY sequence", (thread_id,)
            ).fetchall()
        return [ConversationEvent(row["kind"], thread_id, row["runtime"], json.loads(row["data_json"]), row["sequence"])
                for row in rows]
