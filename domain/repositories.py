"""Storage contracts used by inventory rules and approval orchestration."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from agent_runtime.dispatch import DispatchRepository

from .models import CategorySummary, Part


class RepositoryConflict(Exception):
    """A write violates a storage uniqueness or integrity constraint."""


class RepositoryBusy(RuntimeError):
    """A concurrent transaction won; retry the whole database-only operation."""


class ExecutionUnavailable(ValueError):
    """Work is unknown, already leased, or no longer owned by this worker."""


class ExecutionBusy(ExecutionUnavailable):
    """A live lease prevents takeover; this is a temporary recovery condition."""

    def __init__(self, retry_after_seconds: int):
        super().__init__("Execution is already running; refresh history and retry resume after its worker releases it")
        self.retry_after_seconds = retry_after_seconds


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
    def get(self, thread_id: str, execution_id: str) -> StoredExecution | None: ...
    def prepare(self, thread_id: str, execution_id: str,
                context: dict[str, Any]) -> StoredExecution: ...
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
    def list_categories(self) -> list[CategorySummary]: ...
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
    def specifications(self, part_id: int) -> list[dict[str, Any]]: ...
    def specifications_for_parts(self, part_ids: list[int]) -> dict[int, list[dict[str, Any]]]: ...
    def specification_reviews(self) -> dict[int, dict[str, Any]]: ...
    def stage_specifications(self, original: Part, facts: list[dict], existing: list[dict]) -> bool: ...
    def apply_specification_review(self, part_id: int) -> None: ...
    def reject_specification_review(self, part_id: int) -> None: ...


class ApprovalRepository(Protocol):
    def insert(self, approval: StoredApproval) -> None: ...
    def get(self, thread_id: str, request_id: str) -> StoredApproval | None: ...
    def set_decision(self, request_id: str, approved: bool) -> None: ...
    def save_result(self, request_id: str, result: dict[str, Any]) -> None: ...


class PartsBinRepository(Protocol):
    """One transaction boundary for inventory, evidence, and agent mutation outcomes.

    Transactions protect read-dependent writes and bind repositories to one unit
    of work. Exceptions roll back the entire unit. Concurrent conflicts may ask
    the caller to retry; only successful single-part effects need atomic receipts.
    """

    storage_id: str
    @property
    def in_transaction(self) -> bool: ...

    inventory: InventoryRepository
    approvals: ApprovalRepository
    operations: OperationRepository
    executions: ExecutionRepository
    dispatch: "DispatchRepository"

    def transaction(self) -> AbstractContextManager["PartsBinRepository"]: ...
