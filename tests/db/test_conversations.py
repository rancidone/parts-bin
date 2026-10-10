"""Conversation publication guarantees needed across worker replacement."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_runtime import ConversationEvent, RuntimeSelectionError
from db.conversations import SQLiteConversationRepository


def test_concurrent_duplicate_delivery_keeps_one_event_and_thread(tmp_path):
    path = tmp_path / "events.db"
    store = SQLiteConversationRepository(path)
    event = ConversationEvent("assistant_text", "thread", "openai", {"text": "saved"})

    def deliver(_index):
        return store.append_once(event, "execution:result")

    with ThreadPoolExecutor(max_workers=4) as pool:
        delivered = list(pool.map(deliver, range(8)))
    reopened = SQLiteConversationRepository(path)
    assert all(item == delivered[0] for item in delivered)
    assert reopened.events("thread") == [delivered[0]]
    assert reopened.runtime_for("thread") == "openai"
    with pytest.raises(RuntimeSelectionError):
        reopened.create_thread("thread", "local")


def test_concurrent_distinct_delivery_allocates_unique_ordered_sequences(tmp_path):
    store = SQLiteConversationRepository(tmp_path / "events.db")

    def deliver(index):
        return store.append_once(ConversationEvent("assistant_text", "thread", "openai",
                                 {"text": str(index)}), f"event:{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        delivered = list(pool.map(deliver, range(8)))
    assert {event.sequence for event in delivered} == set(range(1, 9))
    assert [event.sequence for event in store.events("thread")] == list(range(1, 9))


def test_event_and_delivery_identity_roll_back_together(tmp_path):
    path = tmp_path / "events.db"
    store = SQLiteConversationRepository(path)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TRIGGER reject_identity BEFORE INSERT ON agent_event_keys
                        BEGIN SELECT RAISE(ABORT, 'identity write failed'); END""")
    event = ConversationEvent("tool_result", "thread", "openai", {"result": {"ok": True}})
    with pytest.raises(sqlite3.IntegrityError, match="identity write failed"):
        store.append_once(event, "result")
    assert store.events("thread") == []
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER reject_identity")
    saved = store.append_once(event, "result")
    assert saved.sequence == 1
    assert SQLiteConversationRepository(path).append_once(event, "result") == saved


def test_delivery_identity_cannot_cross_conversations(tmp_path):
    store = SQLiteConversationRepository(tmp_path / "events.db")
    saved = store.append_once(ConversationEvent("assistant_text", "first", "openai", {"text": "first"}), "result")
    with pytest.raises(ValueError, match="another conversation"):
        store.append_once(ConversationEvent("assistant_text", "other", "openai", {"text": "other"}), "result")
    assert store.events("first") == [saved]
    assert store.events("other") == []
