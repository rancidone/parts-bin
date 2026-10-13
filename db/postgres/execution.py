"""Postgres execution leases, checkpoints and committed addition receipts."""

import json
import math
import time

from domain.repositories import ExecutionBusy, ExecutionUnavailable, StoredExecution, StoredOperation
from .connection import Adapter, access

LEASE_SECONDS = 300


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PostgresOperationRepository(Adapter):
    @access()
    def get(self, operation_id: str) -> StoredOperation | None:
        row = self.execute("SELECT * FROM agent_operations WHERE operation_id = %s", (operation_id,)).fetchone()
        return None if row is None else StoredOperation(row["operation_id"], row["tool_name"],
        json.loads(row["arguments_json"]), json.loads(row["result_json"]))

    @access(write=True)
    def insert(self, operation: StoredOperation) -> None:
        self.execute("INSERT INTO agent_operations VALUES (%s, %s, %s, %s)",
            (operation.operation_id, operation.tool_name, encode(operation.arguments), encode(operation.result)))

    @access(write=True)
    def save_result(self, operation_id: str, result: dict) -> None:
        self.execute("UPDATE agent_operations SET result_json = %s WHERE operation_id = %s", (encode(result), operation_id))


class PostgresExecutionRepository(Adapter):
    @staticmethod
    def record(row) -> StoredExecution:
        return StoredExecution(row["execution_id"], row["thread_id"], json.loads(row["context_json"]), row["status"])

    @access(write=True)
    def prepare(self, thread_id: str, execution_id: str, context: dict) -> StoredExecution:
        """Persist intake without a lease; duplicate delivery never resets progress."""
        if not execution_id or not context.get("input_digest"):
            raise ValueError("Execution identity and input digest are required")
        row = self.execute("SELECT * FROM agent_executions WHERE execution_id = %s",
                           (execution_id,)).fetchone()
        if row is not None:
            record = self.record(row)
            if record.thread_id != thread_id or record.context.get("input_digest") != context["input_digest"]:
                raise ExecutionUnavailable("Execution identity was already used for a different request")
            return record
        if self.execute("SELECT 1 FROM agent_executions WHERE thread_id = %s AND status IN ('ready', 'waiting_model')",
                        (thread_id,)).fetchone():
            raise ExecutionUnavailable("Resume the unfinished execution before starting another message")
        self.execute("INSERT INTO agent_executions VALUES (%s, %s, %s, 'ready', NULL, 0)",
                     (execution_id, thread_id, encode(context)))
        return StoredExecution(execution_id, thread_id, context, "ready")

    @access(write=True)
    def claim(self, thread_id: str, execution_id: str, worker_id: str, context: dict | None = None) -> StoredExecution:
        now = time.time()
        row = self.execute("SELECT * FROM agent_executions WHERE execution_id = %s AND thread_id = %s",
                           (execution_id, thread_id)).fetchone()
        if row is None:
            if context is None:
                raise ExecutionUnavailable("Unknown execution for this conversation")
            if self.execute("SELECT 1 FROM agent_executions WHERE thread_id = %s AND status IN ('ready', 'waiting_model')",
                            (thread_id,)).fetchone():
                raise ExecutionUnavailable("Resume the unfinished execution before starting another message")
            self.execute("INSERT INTO agent_executions VALUES (%s, %s, %s, 'ready', %s, %s)",
                         (execution_id, thread_id, encode(context), worker_id, now + LEASE_SECONDS))
            return StoredExecution(execution_id, thread_id, context, "ready")
        if row["status"] in {"completed", "failed"}:
            return self.record(row)
        if row["worker_id"] is not None and row["lease_until"] > now:
            raise ExecutionBusy(max(1, math.ceil(row["lease_until"] - now)))
        self.execute("UPDATE agent_executions SET worker_id = %s, lease_until = %s WHERE execution_id = %s",
                     (worker_id, now + LEASE_SECONDS, execution_id))
        return self.record(row)

    @access()
    def for_approval(self, thread_id: str, request_id: str) -> StoredExecution | None:
        row = self.execute("""SELECT e.* FROM agent_executions e
            JOIN agent_execution_approvals a ON e.execution_id = a.execution_id
            WHERE e.thread_id = %s AND a.request_id = %s""", (thread_id, request_id)).fetchone()
        return None if row is None else self.record(row)

    @access()
    def assert_owned(self, execution_id: str, worker_id: str) -> None:
        if not self.execute("SELECT 1 FROM agent_executions WHERE execution_id = %s AND worker_id = %s AND lease_until > %s",
                            (execution_id, worker_id, time.time())).fetchone():
            raise ExecutionUnavailable("Execution lease expired or belongs to another worker")

    @access(write=True)
    def save(self, execution_id: str, worker_id: str, context: dict, status: str) -> None:
        cursor = self.execute("""UPDATE agent_executions SET context_json = %s, status = %s, lease_until = %s
            WHERE execution_id = %s AND worker_id = %s AND lease_until > %s""",
            (encode(context), status, time.time() + LEASE_SECONDS, execution_id, worker_id, time.time()))
        if cursor.rowcount != 1:
            raise ExecutionUnavailable("Execution lease expired or belongs to another worker")
        if context.get("approval_id"):
            self.execute("INSERT INTO agent_execution_approvals VALUES (%s, %s) ON CONFLICT(request_id) DO NOTHING",
                         (context["approval_id"], execution_id))

    @access(write=True)
    def release(self, execution_id: str, worker_id: str) -> None:
        self.execute("UPDATE agent_executions SET worker_id = NULL, lease_until = 0 WHERE execution_id = %s AND worker_id = %s",
                     (execution_id, worker_id))
