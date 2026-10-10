"""SQLite adapters for the domain storage contracts; SQL stays in this package."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from domain.models import CategorySummary, Part
from domain.repositories import ApprovalRepository, InventoryRepository, OperationRepository, ExecutionRepository, RepositoryConflict, StoredApproval

from . import persistence, specifications
from .execution import SQLiteExecutionRepository, SQLiteOperationRepository


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class SQLiteInventoryRepository:
    def __init__(self, database: str | Path | persistence.TransactionConnection):
        self.database = database

    def search(self, filters: dict) -> list[Part]:
        return [Part.from_row(row) for row in persistence.query(self.database, filters)]

    def list_categories(self) -> list[CategorySummary]:
        return [CategorySummary(**row) for row in persistence.list_categories(self.database)]

    def get(self, part_id: int) -> Part | None:
        row = persistence.get_by_id(self.database, part_id)
        return None if row is None else Part.from_row(row)

    def insert(self, fields: dict) -> int:
        try:
            return persistence.insert_part(self.database, fields)
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("Inventory uniqueness constraint violated") from exc

    def increment_stock(self, part_id: int, quantity: int) -> None:
        persistence.increment_stock(self.database, part_id, quantity)

    def replace_parts(self, updates: list[tuple[int, dict]]) -> None:
        try:
            persistence.replace_parts_atomic(self.database, updates)
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("Inventory uniqueness constraint violated") from exc

    def delete(self, part_id: int) -> None:
        persistence.delete_part(self.database, part_id)

    def list_pending_reviews(self) -> dict[int, dict]:
        return persistence.list_pending_reviews(self.database)

    def save_pending_review(self, part_id: int, fields: dict, provenance: list[dict]) -> None:
        persistence.save_pending_review(self.database, part_id, fields, provenance)

    def stage_enrichment_if_unchanged(self, original: Part, fields: dict, provenance: list[dict]) -> bool:
        return persistence.stage_enrichment_if_unchanged(self.database, original.id, vars(original), fields, provenance)

    def clear_pending_review(self, part_id: int, fields: list[str] | None = None) -> None:
        persistence.clear_pending_review(self.database, part_id, fields)

    def update_with_provenance(self, part_id: int, fields: dict, provenance: list[dict]) -> None:
        try:
            persistence.update_fields_with_provenance(self.database, part_id, fields, provenance)
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("Inventory integrity constraint violated") from exc

    def list_provenance(self, part_id: int) -> list[dict]:
        return persistence.list_field_provenance(self.database, part_id)

    def specifications(self, part_id: int) -> list[dict]:
        return specifications.facts(self.database, part_id)

    def specification_reviews(self) -> dict[int, dict]:
        return specifications.reviews(self.database)

    def stage_specifications(self, original: Part, facts: list[dict], existing: list[dict]) -> bool:
        return specifications.stage(self.database, original, facts, existing)

    def apply_specification_review(self, part_id: int) -> None:
        specifications.apply(self.database, part_id)

    def reject_specification_review(self, part_id: int) -> None:
        specifications.reject(self.database, part_id)


class SQLiteApprovalRepository:
    def __init__(self, database: str | Path | persistence.TransactionConnection):
        self.database = database

    @contextmanager
    def _connection(self):
        conn = persistence._connect(self.database)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def insert(self, approval: StoredApproval) -> None:
        with self._connection() as conn:
            conn.execute("""INSERT INTO agent_approvals
                (request_id, thread_id, tool_name, arguments_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?)""", (approval.request_id, approval.thread_id,
                approval.tool_name, _encode(approval.arguments), _encode(approval.snapshot)))

    def get(self, thread_id: str, request_id: str) -> StoredApproval | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM agent_approvals WHERE request_id = ? AND thread_id = ?",
                               (request_id, thread_id)).fetchone()
        if row is None:
            return None
        return StoredApproval(row["request_id"], row["thread_id"], row["tool_name"],
            json.loads(row["arguments_json"]), json.loads(row["snapshot_json"]),
            None if row["decision"] is None else bool(row["decision"]),
            None if row["result_json"] is None else json.loads(row["result_json"]))

    def set_decision(self, request_id: str, approved: bool) -> None:
        with self._connection() as conn:
            conn.execute("UPDATE agent_approvals SET decision = ? WHERE request_id = ?",
                         (int(approved), request_id))

    def save_result(self, request_id: str, result: dict) -> None:
        with self._connection() as conn:
            conn.execute("UPDATE agent_approvals SET result_json = ? WHERE request_id = ?",
                         (_encode(result), request_id))


class _SQLiteSavepoint:
    def __init__(self):
        self.rejected = False

    def rollback(self) -> None:
        self.rejected = True


class SQLitePartsBinRepository:
    inventory: InventoryRepository
    approvals: ApprovalRepository
    operations: OperationRepository
    executions: ExecutionRepository

    def __init__(self, database: str | Path):
        self.database = database
        self.storage_id = f"sqlite:{Path(database).resolve()}"
        persistence.init_db(database)
        conn = persistence._connect(database)
        try:
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS agent_approvals (
                    request_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    decision INTEGER CHECK(decision IN (0, 1)),
                    result_json TEXT
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS agent_operations (
                    operation_id TEXT PRIMARY KEY, tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL, result_json TEXT NOT NULL)""")
                conn.execute("""CREATE TABLE IF NOT EXISTS agent_executions (
                    execution_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
                    context_json TEXT NOT NULL, status TEXT NOT NULL,
                    worker_id TEXT, lease_until REAL NOT NULL)""")
                conn.execute("""CREATE TABLE IF NOT EXISTS agent_execution_approvals (
                    request_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL)""")
        finally:
            conn.close()
        self.inventory = SQLiteInventoryRepository(database)
        self.approvals = SQLiteApprovalRepository(database)
        self.operations = SQLiteOperationRepository(database)
        self.executions = SQLiteExecutionRepository(database)
        self._connection: persistence.TransactionConnection | None = None

    @classmethod
    def _bound(cls, owner: SQLitePartsBinRepository, connection: persistence.TransactionConnection) -> SQLitePartsBinRepository:
        instance = object.__new__(cls)
        instance.database = owner.database
        instance.storage_id = owner.storage_id
        instance.inventory = SQLiteInventoryRepository(connection)
        instance.approvals = SQLiteApprovalRepository(connection)
        instance.operations = SQLiteOperationRepository(connection)
        instance.executions = SQLiteExecutionRepository(connection)
        instance._connection = connection
        return instance

    @contextmanager
    def transaction(self):
        if self._connection is not None:
            raise RuntimeError("Start transactions on the root repository, not a bound unit of work")
        with persistence.transaction(self.database) as connection:
            yield self._bound(self, connection)

    @contextmanager
    def savepoint(self):
        if self._connection is None:
            raise RuntimeError("Savepoints require an active transaction")
        name = f"mutation_{uuid4().hex}"
        point = _SQLiteSavepoint()
        self._connection.execute(f"SAVEPOINT {name}")
        try:
            yield point
        except BaseException:
            self._connection.execute(f"ROLLBACK TO {name}")
            raise
        finally:
            if point.rejected:
                self._connection.execute(f"ROLLBACK TO {name}")
            self._connection.execute(f"RELEASE {name}")
