"""Application host for the normalized agent event protocol.

The gateway deliberately knows nothing about Parts Bin records or SQLite.  It
hosts an OpenAI runtime, owns its lifecycle, and translates its
durable ``ConversationEvent`` objects to the one stream consumed by the UI.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import AsyncIterator, Callable
from time import perf_counter
from typing import Awaitable
from uuid import uuid4

from .models import ApprovalResponse, ConversationEvent, ImageInput, RuntimeName
from .runtime import OpenAIResponsesRuntime
from .store import ConversationRepository, UnsupportedRuntimeError
from .telemetry import AgentTelemetry

RuntimeFactory = Callable[[], Awaitable[OpenAIResponsesRuntime] | OpenAIResponsesRuntime]


class AgentGateway:
    """Thread routing, runtime lifecycle, and durable event replay only."""

    def __init__(self, store: ConversationRepository, make_runtime: RuntimeFactory,
                 *, telemetry: AgentTelemetry | None = None) -> None:
        self.store = store
        self._make_runtime = make_runtime
        self._runtime: OpenAIResponsesRuntime | None = None
        self.telemetry = telemetry or AgentTelemetry()

    def create_thread(self) -> str:
        thread_id = uuid4().hex
        self.store.create_thread(thread_id, "openai")
        self.telemetry.runtime_selected(thread_id, "openai")
        return thread_id

    async def submit(self, thread_id: str, text: str, *, image: ImageInput | None = None,
                     on_event: Callable[[ConversationEvent], None] | None = None) -> tuple[ConversationEvent, ...]:
        runtime_name = self._runtime_name(thread_id)
        started = perf_counter()
        try:
            runtime = await self._runtime_for(thread_id)
            return (await runtime.run(thread_id, text, image=image, on_event=on_event)).events
        except Exception as exc:  # Provider/process failures share the event contract.
            events = self._failure(thread_id, runtime_name, "runtime_startup_failed", str(exc), latency_ms=(perf_counter() - started) * 1000)
            if on_event is not None:
                for event in events:
                    on_event(event)
            return events

    async def respond_to_approval(self, thread_id: str, response: ApprovalResponse, *,
                                  on_event: Callable[[ConversationEvent], None] | None = None) -> tuple[ConversationEvent, ...]:
        runtime_name = self._runtime_name(thread_id)
        started = perf_counter()
        try:
            runtime = await self._runtime_for(thread_id)
            return (await runtime.run(thread_id, "", approval_response=response, on_event=on_event)).events
        except Exception as exc:
            events = self._failure(thread_id, runtime_name, "runtime_failed", str(exc), latency_ms=(perf_counter() - started) * 1000)
            if on_event is not None:
                for event in events:
                    on_event(event)
            return events

    def submit_stream(self, thread_id: str, text: str, *, image: ImageInput | None = None) -> AsyncIterator[ConversationEvent]:
        self._runtime_name(thread_id)
        return self._stream(lambda receive: self.submit(thread_id, text, image=image, on_event=receive))

    async def resume(self, thread_id: str, execution_id: str, *, image: ImageInput | None = None,
                     on_event: Callable[[ConversationEvent], None] | None = None) -> tuple[ConversationEvent, ...]:
        runtime_name = self._runtime_name(thread_id)
        started = perf_counter()
        try:
            runtime = await self._runtime_for(thread_id)
            return (await runtime.run(thread_id, "", execution_id=execution_id,
                                      image=image, on_event=on_event)).events
        except Exception as exc:
            events = self._failure(thread_id, runtime_name, "execution_resume_failed", str(exc),
                                   latency_ms=(perf_counter() - started) * 1000)
            if on_event is not None:
                for event in events:
                    on_event(event)
            return events

    def resume_stream(self, thread_id: str, execution_id: str, *, image: ImageInput | None = None) -> AsyncIterator[ConversationEvent]:
        self._runtime_name(thread_id)
        return self._stream(lambda receive: self.resume(thread_id, execution_id, image=image, on_event=receive))

    def approval_stream(self, thread_id: str, response: ApprovalResponse) -> AsyncIterator[ConversationEvent]:
        self._runtime_name(thread_id)
        return self._stream(lambda receive: self.respond_to_approval(thread_id, response, on_event=receive))

    async def _stream(self, run) -> AsyncIterator[ConversationEvent]:
        queue: asyncio.Queue[ConversationEvent | None] = asyncio.Queue()

        async def produce():
            try:
                await run(queue.put_nowait)
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(produce())
        try:
            while (event := await queue.get()) is not None:
                yield event
            await task
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def events(self, thread_id: str, *, after: int = 0) -> tuple[ConversationEvent, ...]:
        if self.store.runtime_for(thread_id) is None:
            raise KeyError(thread_id)
        return tuple(event for event in self.store.events(thread_id) if (event.sequence or 0) > after)

    async def close(self) -> None:
        """Release runtime-owned connections/processes during application shutdown."""
        if self._runtime is not None:
            closer = getattr(self._runtime.transport, "close", None)
            if closer is not None:
                result = closer()
                if hasattr(result, "__await__"):
                    await result
            self._runtime = None

    async def _runtime_for(self, thread_id: str) -> OpenAIResponsesRuntime:
        self._runtime_name(thread_id)
        if self._runtime is None:
            built = self._make_runtime()
            self._runtime = await built if hasattr(built, "__await__") else built
        return self._runtime

    def _runtime_name(self, thread_id: str) -> RuntimeName:
        runtime_name = self.store.runtime_for(thread_id)
        if runtime_name is None:
            raise KeyError(thread_id)
        if runtime_name != "openai":
            raise UnsupportedRuntimeError(
                "This conversation uses a retired provider and is read-only. Start a new OpenAI conversation."
            )
        return runtime_name

    def _failure(self, thread_id: str, runtime: RuntimeName, code: str, message: str, *, latency_ms: float) -> tuple[ConversationEvent, ...]:
        self.telemetry.runtime_failure(thread_id, runtime, code)
        self.telemetry.turn_finished(thread_id, runtime, latency_ms=latency_ms, status="failed")
        error = self.store.append(ConversationEvent("error", thread_id, runtime, {"code": code, "message": message}))
        completed = self.store.append(ConversationEvent("completed", thread_id, runtime, {"status": "failed"}))
        return error, completed
