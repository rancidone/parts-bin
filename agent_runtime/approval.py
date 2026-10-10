"""Durable server-owned approvals and atomic inventory mutation outcomes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from domain import PartsBinService
from domain.repositories import PartsBinRepository, StoredApproval
from tools import ApprovalReceipt, PartsBinToolRegistry, ToolExecutionContext


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    thread_id: str
    tool_name: str
    arguments: dict[str, Any]


class ApprovalEngine:
    """Persist decisions beside inventory; commit each approved effect and result together."""

    def __init__(self, repository: PartsBinRepository) -> None:
        self.repository = repository

    def request(self, thread_id: str, tool_name: str, arguments: dict[str, Any],
                *, service: PartsBinService) -> ApprovalRequest:
        self._check_database(service)
        targets = PartsBinToolRegistry.approval_targets(tool_name, arguments)
        request = ApprovalRequest(uuid4().hex, thread_id, tool_name, json.loads(json.dumps(arguments)))
        with service.transaction() as (bound, repository):
            repository.approvals.insert(StoredApproval(request.request_id, thread_id,
                tool_name, request.arguments, bound.approval_snapshot(targets)))
        return request

    def _check_database(self, service: PartsBinService) -> None:
        if service.repository.storage_id != self.repository.storage_id:
            raise ValueError("Approvals and inventory must share one transactional database")

    @staticmethod
    def _request(record: StoredApproval) -> ApprovalRequest:
        return ApprovalRequest(record.request_id, record.thread_id, record.tool_name, record.arguments)

    def decide(self, thread_id: str, request_id: str, approved: bool) -> ApprovalRequest:
        if type(approved) is not bool:
            raise ValueError("Approval decision must be a boolean")
        with self.repository.transaction() as repository:
            record = repository.approvals.get(thread_id, request_id)
            if record is None:
                raise ValueError("Unknown approval request")
            if record.decision is not None and record.decision != approved:
                raise ValueError("Approval decision has already been recorded")
            repository.approvals.set_decision(request_id, approved)
            return self._request(record)

    async def execute(self, request: ApprovalRequest, registry: PartsBinToolRegistry, *,
                      execution_id: str | None = None, worker_id: str | None = None) -> dict:
        """Replay saved results before checking targets; never reconstruct arguments."""
        self._check_database(registry.service)
        targets = registry.approval_targets(request.tool_name, request.arguments)
        with registry.service.transaction() as (service, repository):
            if execution_id and worker_id:
                repository.executions.assert_owned(execution_id, worker_id)
            record = repository.approvals.get(request.thread_id, request.request_id)
            if record is None or record.decision is not True or self._request(record) != request:
                raise ValueError("Operation has no matching approval")
            if record.result is not None:
                return record.result
            if service.approval_snapshot(targets) != record.snapshot:
                result = {"ok": False, "error": {"code": "conflict",
                    "message": "Inventory or pending review changed; request a fresh approval", "details": {}}}
            else:
                # Registry dispatch retains domain validation. No model or retrieval
                # calls occur inside this transaction: only approval-gated mutations.
                bound = PartsBinToolRegistry(service, approval_checker=lambda _name, _args: True)
                with repository.savepoint() as point:
                    result = await bound.execute(request.tool_name, request.arguments,
                        context=ToolExecutionContext(ApprovalReceipt.issue(request.tool_name, request.arguments)))
                    if not result["ok"]:
                        point.rollback()
            repository.approvals.save_result(request.request_id, result)
            return result
