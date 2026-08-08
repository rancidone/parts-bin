"""Application host for the normalized agent event protocol.

The gateway deliberately knows nothing about Parts Bin records or SQLite.  It
selects an already-composed runtime, owns its lifecycle, and translates its
durable ``ConversationEvent`` objects to the one stream consumed by the UI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from time import perf_counter
from typing import Awaitable
from uuid import uuid4

from .models import ApprovalResponse, ConversationEvent, ImageInput, RuntimeName
from .runtime import AgentRuntime
from .store import ConversationStore
from .telemetry import AgentTelemetry

RuntimeFactory = Callable[[RuntimeName], Awaitable[AgentRuntime] | AgentRuntime]


class AgentGateway:
    """Thread routing, runtime lifecycle, and durable event replay only."""

    def __init__(self, store: ConversationStore, make_runtime: RuntimeFactory,
                 *, telemetry: AgentTelemetry | None = None) -> None:
        self.store = store
        self._make_runtime = make_runtime
        self._runtimes: dict[RuntimeName, AgentRuntime] = {}
        self.telemetry = telemetry or AgentTelemetry()

    def create_thread(self, runtime: RuntimeName) -> str:
        thread_id = uuid4().hex
        self.store.create_thread(thread_id, runtime)
        self.telemetry.runtime_selected(thread_id, runtime)
        return thread_id

    async def submit(self, thread_id: str, text: str, *, image: ImageInput | None = None) -> tuple[ConversationEvent, ...]:
        runtime_name = self._runtime_name(thread_id)
        started = perf_counter()
        try:
            runtime = await self._runtime_for(thread_id)
            return (await runtime.run(thread_id, text, image=image)).events
        except Exception as exc:  # Provider/process failures share the event contract.
            return self._failure(thread_id, runtime_name, "runtime_startup_failed", str(exc), latency_ms=(perf_counter() - started) * 1000)

    async def respond_to_approval(self, thread_id: str, response: ApprovalResponse) -> tuple[ConversationEvent, ...]:
        runtime_name = self._runtime_name(thread_id)
        started = perf_counter()
        try:
            runtime = await self._runtime_for(thread_id)
            return (await runtime.run(thread_id, "", approval_response=response)).events
        except Exception as exc:
            return self._failure(thread_id, runtime_name, "runtime_failed", str(exc), latency_ms=(perf_counter() - started) * 1000)

    def events(self, thread_id: str, *, after: int = 0) -> tuple[ConversationEvent, ...]:
        if self.store.runtime_for(thread_id) is None:
            raise KeyError(thread_id)
        return tuple(event for event in self.store.events(thread_id) if (event.sequence or 0) > after)

    async def close(self) -> None:
        """Release runtime-owned connections/processes during application shutdown."""
        for runtime in self._runtimes.values():
            closer = getattr(getattr(runtime, "transport", None), "close", None)
            if closer is not None:
                result = closer()
                if hasattr(result, "__await__"):
                    await result
        self._runtimes.clear()

    async def _runtime_for(self, thread_id: str) -> AgentRuntime:
        runtime_name = self._runtime_name(thread_id)
        runtime = self._runtimes.get(runtime_name)
        if runtime is None:
            built = self._make_runtime(runtime_name)
            runtime = await built if hasattr(built, "__await__") else built
            self._runtimes[runtime_name] = runtime
        return runtime

    def _runtime_name(self, thread_id: str) -> RuntimeName:
        runtime_name = self.store.runtime_for(thread_id)
        if runtime_name is None:
            raise KeyError(thread_id)
        return runtime_name

    def _failure(self, thread_id: str, runtime: RuntimeName, code: str, message: str, *, latency_ms: float) -> tuple[ConversationEvent, ...]:
        self.telemetry.runtime_failure(thread_id, runtime, code)
        self.telemetry.turn_finished(thread_id, runtime, latency_ms=latency_ms, status="failed")
        error = self.store.append(ConversationEvent("error", thread_id, runtime, {"code": code, "message": message}))
        completed = self.store.append(ConversationEvent("completed", thread_id, runtime, {"status": "failed"}))
        return error, completed


async def event_stream(events: tuple[ConversationEvent, ...]) -> AsyncIterator[ConversationEvent]:
    """Tiny async adapter kept at the HTTP boundary for SSE streaming."""
    for event in events:
        yield event
