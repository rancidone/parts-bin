"""
Tests for LLM client — pure logic only (no HTTP calls).
"""

import json

import pytest

from llm.client import CHAT_SCHEMA, ConversationHistory, LLMClient, _build_content


class TestBuildContent:
    def test_text_only(self):
        result = _build_content("hello", None)
        assert result == "hello"

    def test_with_image(self):
        result = _build_content("label", "abc123")
        assert isinstance(result, list)
        assert result[0] == {"type": "text", "text": "label"}
        assert result[1]["type"] == "image_url"
        assert "abc123" in result[1]["image_url"]["url"]


class TestConversationHistory:
    def test_append_and_retrieve(self):
        h = ConversationHistory()
        h.append("user", "hello")
        h.append("assistant", "hi")
        msgs = h.messages()
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "hello"}
        assert msgs[1] == {"role": "assistant", "content": "hi"}

    def test_clear(self):
        h = ConversationHistory()
        h.append("user", "hello")
        h.clear()
        assert h.messages() == []

    def test_evicts_oldest_pair_when_over_cap(self):
        h = ConversationHistory(max_turns=2)
        # Add 2 complete pairs.
        h.append("user", "u1")
        h.append("assistant", "a1")
        h.append("user", "u2")
        h.append("assistant", "a2")
        assert len(h.messages()) == 4

        # Adding a third pair should evict the first.
        h.append("user", "u3")
        h.append("assistant", "a3")
        msgs = h.messages()
        assert len(msgs) == 4
        assert msgs[0]["content"] == "u2"
        assert msgs[1]["content"] == "a2"
        assert msgs[2]["content"] == "u3"
        assert msgs[3]["content"] == "a3"

    def test_messages_returns_copy(self):
        h = ConversationHistory()
        h.append("user", "hello")
        msgs = h.messages()
        msgs.clear()
        assert len(h.messages()) == 1

    def test_single_turn_within_cap(self):
        h = ConversationHistory(max_turns=1)
        h.append("user", "u1")
        h.append("assistant", "a1")
        assert len(h.messages()) == 2

    def test_over_cap_by_one_pair(self):
        h = ConversationHistory(max_turns=1)
        h.append("user", "u1")
        h.append("assistant", "a1")
        h.append("user", "u2")
        h.append("assistant", "a2")
        msgs = h.messages()
        assert len(msgs) == 2
        assert msgs[0]["content"] == "u2"


@pytest.mark.asyncio
class TestChatHistory:
    async def test_chat_persists_structured_assistant_context(self, monkeypatch):
        client = LLMClient(base_url="http://localhost:8080")
        history = ConversationHistory()
        result = {
            "response": "Added it.",
            "db_action": {
                "type": "upsert",
                "id": None,
                "items": None,
                "part_category": "resistor",
                "profile": "passive",
                "value": "10k",
                "package": "0402",
                "part_number": None,
                "quantity": 20,
                "description": None,
            },
        }

        async def fake_extract(messages, schema):
            assert schema == CHAT_SCHEMA
            return result

        monkeypatch.setattr(client, "_extract_with_retry", fake_extract)

        returned = await client.chat("add 20 10k 0402 resistors", None, history, [])

        assert returned == result
        messages = history.messages()
        assert messages[0] == {"role": "user", "content": "add 20 10k 0402 resistors"}
        assert messages[1]["role"] == "assistant"
        assert json.loads(messages[1]["content"]) == result
