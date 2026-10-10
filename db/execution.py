"""SQLite persistence for execution checkpoints and committed addition outcomes."""

import json
import time
from contextlib import contextmanager

from domain.repositories import ExecutionUnavailable, StoredExecution, StoredOperation
from . import persistence

LEASE_SECONDS = 300


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class SQLiteOperationRepository:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def connection(self):
        conn = persistence._connect(self.database)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, operation_id: str) -> StoredOperation | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM agent_operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return None if row is None else StoredOperation(row["operation_id"], row["tool_name"],
            json.loads(row["arguments_json"]), json.loads(row["result_json"]))

    def insert(self, operation: StoredOperation) -> None:
        with self.connection() as conn:
            conn.execute("INSERT INTO agent_operations VALUES (?, ?, ?, ?)",
                (operation.operation_id, operation.tool_name, encode(operation.arguments), encode(operation.result)))

    def save_result(self, operation_id: str, result: dict) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE agent_operations SET result_json = ? WHERE operation_id = ?", (encode(result), operation_id))


class SQLiteExecutionRepository:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def connection(self):
        conn = persistence._connect(self.database)
        try:
            with conn:
                if not isinstance(conn, persistence.TransactionConnection):
                    conn.execute("BEGIN IMMEDIATE")
                yield conn
        finally:
            conn.close()

    @staticmethod
    def record(row) -> StoredExecution:
        return StoredExecution(row["execution_id"], row["thread_id"], json.loads(row["context_json"]), row["status"])

    def claim(self, thread_id: str, execution_id: str, worker_id: str, context: dict | None = None) -> StoredExecution:
        now = time.time()
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM agent_executions WHERE execution_id = ? AND thread_id = ?",
                               (execution_id, thread_id)).fetchone()
            if row is None:
                if context is None:
                    raise ExecutionUnavailable("Unknown execution for this conversation")
                if conn.execute("SELECT 1 FROM agent_executions WHERE thread_id = ? AND status = 'ready'",
                                (thread_id,)).fetchone():
                    raise ExecutionUnavailable("Resume the unfinished execution before starting another message")
                conn.execute("INSERT INTO agent_executions VALUES (?, ?, ?, 'ready', ?, ?)",
                             (execution_id, thread_id, encode(context), worker_id, now + LEASE_SECONDS))
                return StoredExecution(execution_id, thread_id, context, "ready")
            if row["status"] in {"completed", "failed"}:
                return self.record(row)
            if row["worker_id"] is not None and row["lease_until"] > now:
                raise ExecutionUnavailable("Execution is already running; retry after its worker releases it")
            conn.execute("UPDATE agent_executions SET worker_id = ?, lease_until = ? WHERE execution_id = ?",
                         (worker_id, now + LEASE_SECONDS, execution_id))
            return self.record(row)

    def for_approval(self, thread_id: str, request_id: str) -> StoredExecution | None:
        with self.connection() as conn:
            row = conn.execute("""SELECT e.* FROM agent_executions e
                JOIN agent_execution_approvals a ON e.execution_id = a.execution_id
                WHERE e.thread_id = ? AND a.request_id = ?""", (thread_id, request_id)).fetchone()
        return None if row is None else self.record(row)

    def assert_owned(self, execution_id: str, worker_id: str) -> None:
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM agent_executions WHERE execution_id = ? AND worker_id = ? AND lease_until > ?",
                                (execution_id, worker_id, time.time())).fetchone():
                raise ExecutionUnavailable("Execution lease expired or belongs to another worker")

    def save(self, execution_id: str, worker_id: str, context: dict, status: str) -> None:
        with self.connection() as conn:
            cursor = conn.execute("""UPDATE agent_executions SET context_json = ?, status = ?, lease_until = ?
                WHERE execution_id = ? AND worker_id = ? AND lease_until > ?""",
                (encode(context), status, time.time() + LEASE_SECONDS, execution_id, worker_id, time.time()))
            if cursor.rowcount != 1:
                raise ExecutionUnavailable("Execution lease expired or belongs to another worker")
            if context.get("approval_id"):
                conn.execute("INSERT OR IGNORE INTO agent_execution_approvals VALUES (?, ?)",
                             (context["approval_id"], execution_id))

    def release(self, execution_id: str, worker_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE agent_executions SET worker_id = NULL, lease_until = 0 WHERE execution_id = ? AND worker_id = ?",
                         (execution_id, worker_id))
