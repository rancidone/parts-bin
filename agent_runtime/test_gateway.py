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


@pytest.mark.asyncio
async def test_gateway_streams_tool_activity_before_model_finishes(tmp_path):
    import asyncio
    from agent_runtime import ToolCall
    release = asyncio.Event()
    calls = 0

    class PausedTransport:
        async def complete(self, request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelTurn(tool_calls=(ToolCall("search_parts", {}, "search"),))
            await release.wait()
            return ModelTurn("Finished")

    store = ConversationStore(tmp_path / "conversation.db")
    gateway = AgentGateway(store, lambda _: OpenAIResponsesRuntime(PausedTransport(),
        registry=PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db")), store=store, approvals=ApprovalEngine()))
    thread = gateway.create_thread("openai")
    stream = gateway.submit_stream(thread, "find parts")
    received = []
    for kind in ["user_message", "tool_call", "tool_result"]:
        event = await asyncio.wait_for(anext(stream), 1)
        assert event.kind == kind
        received.append(event)
    assert not release.is_set()
    assert gateway.events(thread) == tuple(received)
    release.set()
    assert [event.kind async for event in stream] == ["assistant_text", "completed"]


@pytest.mark.asyncio
async def test_gateway_preserves_partial_events_on_stream_failure(tmp_path):
    class BrokenTransport:
        async def complete(self, request):
            raise RuntimeError("provider stopped")
    store = ConversationStore(tmp_path / "conversation.db")
    gateway = AgentGateway(store, lambda _: OpenAIResponsesRuntime(BrokenTransport(),
        registry=PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db")), store=store, approvals=ApprovalEngine()))
    thread = gateway.create_thread("openai")
    events = [event async for event in gateway.submit_stream(thread, "hello")]
    assert [event.kind for event in events] == ["user_message", "error", "completed"]
    assert gateway.events(thread) == tuple(events)
