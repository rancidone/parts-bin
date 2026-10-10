"""SQLite adapter for conversation identity and idempotent event publication."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from agent_runtime.models import ConversationEvent, ConversationSummary, RuntimeName
from agent_runtime.store import RuntimeSelectionError, UnsupportedRuntimeError


class SQLiteConversationRepository:
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

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_thread(self, thread_id: str, runtime: RuntimeName) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
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

    def threads(self) -> list[ConversationSummary]:
        """List retained threads without changing or attempting to run any of them."""
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT t.rowid AS thread_order, t.thread_id, t.runtime,
                       COALESCE(MAX(e.sequence), 0) AS last_sequence
                FROM agent_threads t
                LEFT JOIN agent_events e ON e.thread_id = t.thread_id
                GROUP BY t.rowid, t.thread_id, t.runtime
                ORDER BY thread_order DESC
            """).fetchall()
            summaries = []
            for row in rows:
                events = conn.execute("""
                    SELECT kind, data_json FROM agent_events
                    WHERE thread_id = ? AND kind IN ('user_message', 'assistant_text')
                    ORDER BY sequence
                """, (row["thread_id"],)).fetchall()
                summaries.append(ConversationSummary(
                    row["thread_id"], row["runtime"], _conversation_title(events), row["last_sequence"]
                ))
        return summaries

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


def _conversation_title(rows: list[sqlite3.Row]) -> str:
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
