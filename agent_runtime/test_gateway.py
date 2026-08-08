from __future__ import annotations

import pytest

from agent_runtime import AgentGateway, ApprovalEngine, ConversationStore, ModelTurn, OpenAIResponsesRuntime
from agent_runtime.runtime import ModelRequest
from domain import PartsBinService
from tools import PartsBinToolRegistry


class TextTransport:
    async def complete(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn("Hello from the gateway.")


@pytest.mark.asyncio
async def test_gateway_persists_and_resumes_one_normalized_stream(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")

    def make_runtime(runtime):
        assert runtime == "openai"
        return OpenAIResponsesRuntime(
            TextTransport(), registry=PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db")),
            store=store, approvals=ApprovalEngine(),
        )

    gateway = AgentGateway(store, make_runtime)
    thread_id = gateway.create_thread("openai")
    emitted = await gateway.submit(thread_id, "hello")
    assert [event.kind for event in emitted] == ["user_message", "assistant_text", "completed"]
    assert gateway.events(thread_id, after=1) == emitted[1:]


@pytest.mark.asyncio
async def test_gateway_turns_runtime_startup_failure_into_events(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    gateway = AgentGateway(store, lambda _runtime: (_ for _ in ()).throw(RuntimeError("not configured")))
    thread_id = gateway.create_thread("codex")
    emitted = await gateway.submit(thread_id, "hello")
    assert [(event.kind, event.data.get("code")) for event in emitted] == [
        ("error", "runtime_startup_failed"), ("completed", None),
    ]
