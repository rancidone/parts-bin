"""Durable stage delivery, independent of HTTP connections and provider callbacks."""

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import log
from domain.repositories import ExecutionBusy
from .models import ApprovalResponse, ConversationEvent, ImageInput
from .approval import ApprovalEngine

POLL_SECONDS = 30
LEASE_SECONDS = 300
MAX_FAILURES = 8
TERMINAL_EVENTS = frozenset({'response.completed', 'response.failed', 'response.cancelled', 'response.incomplete'})


@dataclass(frozen=True)
class ProgressState:
    active: bool
    accepted: bool | None = None


@dataclass(frozen=True)
class ProgressSnapshot:
    events: tuple[ConversationEvent, ...]
    state: ProgressState


@dataclass(frozen=True)
class Delivery:
    execution_id: str
    thread_id: str
    token: str
    wake_version: int
    failures: int


class DispatchRepository(Protocol):
    def accept(self, thread_id: str, execution_id: str, context: dict, reservation: str | None = None): ...
    def progress_snapshot(self, thread_id: str, after: int = 0, execution_id: str | None = None) -> ProgressSnapshot: ...
    def progress_state(self, thread_id: str, execution_id: str | None = None) -> ProgressState: ...
    def has_due(self) -> bool: ...
    def blocked_deliveries(self) -> list[Delivery]: ...
    def claim(self, execution_id: str | None = None, token: str | None = None) -> Delivery | None: ...
    def finish(self, delivery: Delivery, *, failure: str | None = None, retry_after: int = 0) -> bool: ...
    def wake(self, thread_id: str, execution_id: str) -> None: ...
    def notify(self, event_id: str, response_id: str, event_type: str) -> bool: ...
    def reconcile(self) -> int: ...


class DurableDispatch:
    def __init__(self, repository, gateway):
        self.repository = repository
        self.gateway = gateway

    async def submit(self, thread_id: str, execution_id: str, text: str, image: ImageInput | None = None):
        reservation = uuid4().hex if image is not None else None
        await self.gateway.prepare(thread_id, execution_id, text, image=image,
                                   durable=True, reservation=reservation)
        if reservation is not None:
            # The intake transaction reserves this initial stage while its photo
            # exists in memory. A dead HTTP host loses the photo, never persists it.
            delivery = self.repository.dispatch.claim(execution_id, reservation)
            if delivery is not None:
                await self.run_delivery(delivery, image=image)
        return execution_id

    def resume(self, thread_id: str, execution_id: str):
        self.gateway._runtime_name(thread_id)
        self.repository.dispatch.wake(thread_id, execution_id)

    def decide(self, thread_id: str, response: ApprovalResponse):
        self.gateway._runtime_name(thread_id)
        with self.repository.transaction() as unit:
            record = unit.executions.for_approval(thread_id, response.request_id)
            if record is None:
                raise ValueError('Unknown approval execution for this conversation')
            ApprovalEngine(unit).decide(thread_id, response.request_id, response.approved)
            unit.dispatch.wake(thread_id, record.execution_id)
        return record.execution_id

    async def run_once(self) -> bool:
        if not self.repository.dispatch.has_due():
            return False
        delivery = self.repository.dispatch.claim()
        if delivery is None:
            return False
        await self.run_delivery(delivery)
        return True

    async def run_delivery(self, delivery: Delivery, *, image: ImageInput | None = None):
        try:
            await self.gateway.advance_background(delivery.thread_id, delivery.execution_id, image=image)
        except asyncio.CancelledError:
            # A killed process is recovered by lease expiry; graceful shutdown
            # makes it available immediately without resetting the checkpoint.
            self.repository.dispatch.finish(delivery, failure='worker_interrupted', retry_after=1)
            raise
        except ExecutionBusy as exc:
            self.repository.dispatch.finish(delivery, failure='execution_busy', retry_after=exc.retry_after_seconds)
        except Exception as exc:
            # Never store exception messages: HTTP/provider errors can contain
            # credentials, request bodies or signed supplier URLs.
            blocked = self.repository.dispatch.finish(delivery, failure=type(exc).__name__)
            log.get_logger('parts_bin.worker').error('Stage delivery failed', extra={
                'execution_id': delivery.execution_id, 'error_type': type(exc).__name__,
                'attempt': delivery.failures + 1, 'blocked': blocked})
            if blocked:
                self.publish_blocked()
        else:
            self.repository.dispatch.finish(delivery)

    def publish_blocked(self):
        for delivery in self.repository.dispatch.blocked_deliveries():
            for kind, data in [('error', {'code': 'dispatch_blocked', 'message':
                'Repeated worker failures. Check worker logs, then resume this request; do not resend it.'}),
                ('completed', {'status': 'busy', 'code': 'dispatch_blocked'})]:
                self.gateway.store.append_once(ConversationEvent(kind, delivery.thread_id, 'openai',
                    {**data, 'execution_id': delivery.execution_id}),
                    f'{delivery.execution_id}:blocked:{delivery.wake_version}:{kind}')

    def reconcile(self):
        repaired = self.repository.dispatch.reconcile()
        self.publish_blocked()
        return repaired

    async def follow(self, thread_id: str, execution_id: str, *, after: int = 0):
        """Read persisted progress; disconnecting cannot cancel accepted work."""
        while True:
            for event in self.gateway.events(thread_id, after=after):
                after = max(after, event.sequence or 0)
                if event.data.get('execution_id') != execution_id:
                    continue
                yield event
                if event.kind != 'completed':
                    continue
                if event.data.get('status') in {'completed', 'failed'}:
                    return
                if event.data.get('status') == 'awaiting_approval':
                    record = self.repository.executions.get(thread_id, execution_id)
                    approval_id = record.context.get('approval_id')
                    approval = self.repository.approvals.get(thread_id, approval_id) if approval_id else None
                    if record.status == 'awaiting_approval' and approval and approval.decision is None:
                        return
                if event.data.get('code') == 'dispatch_blocked' and any(
                    item.execution_id == execution_id for item in self.repository.dispatch.blocked_deliveries()
                ):
                    return
            await asyncio.sleep(0.5)
