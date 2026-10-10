"""Storage contracts used by inventory rules and approval orchestration."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from .models import Part


class RepositoryConflict(Exception):
    """A write violates a storage uniqueness or integrity constraint."""


class ExecutionUnavailable(ValueError):
    """Work is unknown, already leased, or no longer owned by this worker."""


@dataclass(frozen=True)
class StoredOperation:
    operation_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class StoredExecution:
    execution_id: str
    thread_id: str
    context: dict[str, Any]
    status: str


class OperationRepository(Protocol):
    def get(self, operation_id: str) -> StoredOperation | None: ...
    def insert(self, operation: StoredOperation) -> None: ...
    def save_result(self, operation_id: str, result: dict[str, Any]) -> None: ...


class ExecutionRepository(Protocol):
    def claim(self, thread_id: str, execution_id: str, worker_id: str,
              context: dict[str, Any] | None = None) -> StoredExecution: ...
    def for_approval(self, thread_id: str, request_id: str) -> StoredExecution | None: ...
    def assert_owned(self, execution_id: str, worker_id: str) -> None: ...
    def save(self, execution_id: str, worker_id: str, context: dict[str, Any], status: str) -> None: ...
    def release(self, execution_id: str, worker_id: str) -> None: ...


@dataclass(frozen=True)
class StoredApproval:
    request_id: str
    thread_id: str
    tool_name: str
    arguments: dict[str, Any]
    snapshot: dict[str, Any]
    decision: bool | None = None
    result: dict[str, Any] | None = None


class InventoryRepository(Protocol):
    """Persist supplied fields; search non-null filters by exact stored equality."""

    def search(self, filters: dict[str, Any]) -> list[Part]: ...
    def get(self, part_id: int) -> Part | None: ...
    def insert(self, fields: dict[str, Any]) -> int: ...
    def increment_stock(self, part_id: int, quantity: int) -> None: ...
    def replace_parts(self, updates: list[tuple[int, dict[str, Any]]]) -> None: ...
    def delete(self, part_id: int) -> None: ...
    def list_pending_reviews(self) -> dict[int, dict[str, Any]]: ...
    def save_pending_review(self, part_id: int, fields: dict[str, Any], provenance: list[dict[str, Any]]) -> None: ...
    def stage_enrichment_if_unchanged(self, original: Part, fields: dict[str, Any], provenance: list[dict[str, Any]]) -> bool: ...
    def clear_pending_review(self, part_id: int, fields: list[str] | None = None) -> None: ...
    def update_with_provenance(self, part_id: int, fields: dict[str, Any], provenance: list[dict[str, Any]]) -> None: ...
    def list_provenance(self, part_id: int) -> list[dict[str, Any]]: ...


class ApprovalRepository(Protocol):
    def insert(self, approval: StoredApproval) -> None: ...
    def get(self, thread_id: str, request_id: str) -> StoredApproval | None: ...
    def set_decision(self, request_id: str, approved: bool) -> None: ...
    def save_result(self, request_id: str, result: dict[str, Any]) -> None: ...


class Savepoint(Protocol):
    def rollback(self) -> None: ...


class PartsBinRepository(Protocol):
    """One transaction boundary for inventory, evidence, and agent mutation outcomes.

    Transactions serialize competing writes and bind the repositories to the same
    unit of work. Exceptions roll back the unit; savepoints allow a rejected domain
    operation to roll back while its failure outcome is retained.
    """

    storage_id: str
    inventory: InventoryRepository
    approvals: ApprovalRepository
    operations: OperationRepository
    executions: ExecutionRepository

    def transaction(self) -> AbstractContextManager["PartsBinRepository"]: ...
    def savepoint(self) -> AbstractContextManager[Savepoint]: ...
