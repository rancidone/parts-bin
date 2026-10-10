"""Conversation publication guarantees needed across worker replacement."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_runtime import ConversationEvent, ConversationSummary, RuntimeSelectionError
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


def test_lists_newest_conversations_with_provider_and_bounded_title(tmp_path):
    path = tmp_path / "events.db"
    store = SQLiteConversationRepository(path)
    store.create_thread("current", "openai")
    store.append(ConversationEvent("user_message", "current", "openai",
                                   {"text": "  Find   all 10k resistors  "}))
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO agent_threads(thread_id, runtime) VALUES (?, ?)", ("historical", "codex"))
    store.append(ConversationEvent("assistant_text", "historical", "codex", {"text": "Preserved answer"}))

    assert store.threads() == [
        ConversationSummary("historical", "codex", "Preserved answer", 1),
        ConversationSummary("current", "openai", "Find all 10k resistors", 1),
    ]

    store.append(ConversationEvent("assistant_text", "current", "openai", {"text": "Later answer"}))
    assert SQLiteConversationRepository(path).threads()[1] == ConversationSummary(
        "current", "openai", "Find all 10k resistors", 2
    )


def test_empty_and_photo_only_conversations_have_usable_titles(tmp_path):
    store = SQLiteConversationRepository(tmp_path / "events.db")
    store.create_thread("empty", "openai")
    store.create_thread("photo", "openai")
    store.append(ConversationEvent("user_message", "photo", "openai", {"text": "", "image": {"media_type": "image/png"}}))
    summaries = {thread.thread_id: thread for thread in store.threads()}
    assert summaries["empty"].title == "Empty conversation"
    assert summaries["photo"].title == "Photo request"
