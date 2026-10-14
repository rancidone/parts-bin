"""Durable server-owned approvals and atomic inventory mutation outcomes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4
import asyncio
from functools import wraps
import time

from domain import BulkUpdateRequest, PartsBinService
from domain.repositories import PartsBinRepository, StoredApproval, RepositoryBusy
from tools import ApprovalReceipt, PartsBinToolRegistry, ToolExecutionContext


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    thread_id: str
    tool_name: str
    arguments: dict[str, Any]


def _database_only(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        for attempt in range(4):
            try:
                return method(*args, **kwargs)
            except RepositoryBusy:
                if attempt == 3:
                    raise
                time.sleep(0.01 * 2 ** attempt)
        raise AssertionError("unreachable")
    return wrapped


class ApprovalEngine:
    """Persist decisions beside inventory; commit each approved effect and result together."""

    def __init__(self, repository: PartsBinRepository) -> None:
        self.repository = repository

    @_database_only
    def preview_bulk_update(self, arguments: dict[str, Any], *, service: PartsBinService) -> dict:
        """Bind the displayed batch preview to the same transaction's approval snapshot."""
        self._check_database(service)
        arguments = json.loads(json.dumps(arguments))
        request_id = uuid4().hex
        with service.transaction() as (bound, repository):
            changes = bound.preview_bulk_update(BulkUpdateRequest(tuple(arguments['part_ids']), arguments['fields']))
            snapshot = bound.approval_snapshot(tuple(arguments['part_ids']))
            repository.approvals.insert(StoredApproval(request_id, 'inventory-bulk',
                'bulk_update_parts', arguments, snapshot))
        return {'request_id': request_id, 'changes': changes,
                'cleared_reviews': [int(key) for key, row in snapshot.items() if row['review']]}

    @_database_only
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

    @_database_only
    def decide(self, thread_id: str, request_id: str, approved: bool) -> ApprovalRequest:
        if type(approved) is not bool:
            raise ValueError("Approval decision must be a boolean")
        with self.repository.transaction() as repository:
            record = repository.approvals.get(thread_id, request_id)
            if record is None:
                raise ValueError("Unknown approval request")
            if record.decision is not None and record.decision != approved:
                raise ValueError("Approval decision has already been recorded")
            execution = repository.executions.for_approval(thread_id, request_id)
            if execution and repository.executions.cancellation_requested(execution.execution_id):
                raise ValueError("This execution was cancelled; start a new request")
            repository.approvals.set_decision(request_id, approved)
            return self._request(record)

    async def execute(self, request: ApprovalRequest, registry: PartsBinToolRegistry, *,
                      execution_id: str | None = None, worker_id: str | None = None) -> dict:
        self._check_database(registry.service)
        for attempt in range(4):
            try:
                if request.tool_name == "bulk_update_parts":
                    return await self._execute_batch(request, registry, execution_id, worker_id)
                return await self._execute_one(request, registry, execution_id, worker_id)
            except RepositoryBusy:
                if attempt == 3:
                    raise
                await asyncio.sleep(0.01 * 2 ** attempt)
        raise AssertionError("unreachable")

    @staticmethod
    def _approved_record(repository, request):
        record = repository.approvals.get(request.thread_id, request.request_id)
        if record is None or record.decision is not True or ApprovalEngine._request(record) != request:
            raise ValueError("Operation has no matching approval")
        return record

    async def _execute_batch(self, request, registry, execution_id, worker_id):
        # A batch approval authorizes individual edits to the displayed snapshots.
        # Deterministic child IDs retain each receipt if the worker dies mid-batch.
        with self.repository.transaction() as repository:
            record = self._approved_record(repository, request)
            if record.result is not None:
                return record.result
            for part_id in request.arguments["part_ids"]:
                child_id = f"{request.request_id}:part:{part_id}"
                if repository.approvals.get(request.thread_id, child_id) is None:
                    repository.approvals.insert(StoredApproval(child_id, request.thread_id, "update_part",
                        {"part_id": part_id, "fields": request.arguments["fields"]},
                        {str(part_id): record.snapshot[str(part_id)]}, decision=True))
                    repository.approvals.set_decision(child_id, True)
        parts, errors = [], []
        for part_id in request.arguments["part_ids"]:
            child = ApprovalRequest(f"{request.request_id}:part:{part_id}", request.thread_id,
                                    "update_part", {"part_id": part_id, "fields": request.arguments["fields"]})
            result = await self.execute(child, registry, execution_id=execution_id, worker_id=worker_id)
            if result["ok"]:
                parts.append(result["result"])
            else:
                errors.append({"part_id": part_id, **result["error"]})
        result = {"ok": True, "result": {"parts": parts, "errors": errors}}
        with self.repository.transaction() as repository:
            if execution_id and worker_id:
                repository.executions.assert_owned(execution_id, worker_id)
            record = self._approved_record(repository, request)
            if record.result is not None:
                return record.result
            repository.approvals.save_result(request.request_id, result)
        return result

    async def _execute_one(self, request, registry, execution_id, worker_id):
        """Commit successful effects with their receipt; reject by full rollback."""
        targets = registry.approval_targets(request.tool_name, request.arguments)
        try:
            with registry.service.transaction() as (service, repository):
                if execution_id and worker_id:
                    repository.executions.assert_owned(execution_id, worker_id)
                record = self._approved_record(repository, request)
                if record.result is not None:
                    return record.result
                if service.approval_snapshot(targets) != record.snapshot:
                    result = {"ok": False, "error": {"code": "conflict",
                        "message": "Inventory or pending review changed; request a fresh approval", "details": {}}}
                else:
                    bound = PartsBinToolRegistry(service, approval_checker=lambda _name, _args: True)
                    result = await bound.execute(request.tool_name, request.arguments,
                        context=ToolExecutionContext(ApprovalReceipt.issue(request.tool_name, request.arguments)))
                if not result["ok"]:
                    raise _RejectedMutation(result)
                repository.approvals.save_result(request.request_id, result)
                return result
        except _RejectedMutation as rejected:
            # No inventory effects survived. A crash here can safely revalidate.
            with self.repository.transaction() as repository:
                if execution_id and worker_id:
                    repository.executions.assert_owned(execution_id, worker_id)
                record = self._approved_record(repository, request)
                if record.result is not None:
                    return record.result
                repository.approvals.save_result(request.request_id, rejected.result)
            return rejected.result


class _RejectedMutation(Exception):
    def __init__(self, result):
        self.result = result
