"""Durable approvals stored beside their inventory effects."""

import json
from domain.repositories import StoredApproval
from .connection import Adapter, access


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PostgresApprovalRepository(Adapter):
    @access(write=True)
    def insert(self, approval):
        self.execute("""INSERT INTO agent_approvals
            (request_id, thread_id, tool_name, arguments_json, snapshot_json, decision, result_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (approval.request_id, approval.thread_id, approval.tool_name, encode(approval.arguments),
             encode(approval.snapshot), None if approval.decision is None else int(approval.decision),
             None if approval.result is None else encode(approval.result)))

    @access()
    def get(self, thread_id, request_id):
        row = self.execute("SELECT * FROM agent_approvals WHERE thread_id = %s AND request_id = %s",
                           (thread_id, request_id)).fetchone()
        return None if row is None else StoredApproval(row['request_id'], row['thread_id'], row['tool_name'],
            json.loads(row['arguments_json']), json.loads(row['snapshot_json']),
            None if row['decision'] is None else bool(row['decision']),
            None if row['result_json'] is None else json.loads(row['result_json']))

    @access(write=True)
    def set_decision(self, request_id, approved):
        self.execute("UPDATE agent_approvals SET decision = %s WHERE request_id = %s", (int(approved), request_id))

    @access(write=True)
    def save_result(self, request_id, result):
        self.execute("UPDATE agent_approvals SET result_json = %s WHERE request_id = %s", (encode(result), request_id))
