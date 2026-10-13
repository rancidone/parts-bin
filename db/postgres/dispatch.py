"""PostgreSQL outbox and webhook inbox in the execution checkpoint transaction."""

import time
from uuid import uuid4

from agent_runtime.dispatch import Delivery, LEASE_SECONDS, MAX_FAILURES, POLL_SECONDS
from domain.repositories import ExecutionUnavailable
from .connection import Adapter, access


class PostgresDispatchRepository(Adapter):
    @access(write=True)
    def accept(self, thread_id, execution_id, context, reservation=None):
        record = self.owner.executions.prepare(thread_id, execution_id, context)
        self.execute('''INSERT INTO agent_dispatch
            (execution_id, available_at, token, lease_until, wake_version, failures, blocked, settled, response_id, last_error)
            VALUES (%s, %s, %s, %s, 0, 0, 0, 0, NULL, NULL)
            ON CONFLICT(execution_id) DO NOTHING''',
            (execution_id, time.time(), reservation, time.time() + LEASE_SECONDS if reservation else 0))
        return record

    @access()
    def has_due(self):
        return self.execute('SELECT 1 FROM agent_dispatch WHERE blocked = 0 AND available_at <= %s AND lease_until <= %s LIMIT 1',
                            (time.time(), time.time())).fetchone() is not None

    @access()
    def blocked_deliveries(self):
        rows = self.execute('''SELECT d.*, e.thread_id FROM agent_dispatch d JOIN agent_executions e
            ON e.execution_id = d.execution_id WHERE d.blocked = 1 ORDER BY d.execution_id LIMIT 100''').fetchall()
        return [Delivery(row['execution_id'], row['thread_id'], '', row['wake_version'], row['failures']) for row in rows]

    @access(write=True)
    def claim(self, execution_id=None, token=None):
        now = time.time()
        if token is not None:
            row = self.execute('''SELECT d.*, e.thread_id FROM agent_dispatch d JOIN agent_executions e
                ON e.execution_id = d.execution_id WHERE d.execution_id = %s AND d.token = %s AND d.lease_until > %s''',
                (execution_id, token, now)).fetchone()
        else:
            row = self.execute('''SELECT d.*, e.thread_id FROM agent_dispatch d JOIN agent_executions e
                ON e.execution_id = d.execution_id WHERE d.blocked = 0 AND d.available_at <= %s
                AND d.lease_until <= %s AND (%s::TEXT IS NULL OR d.execution_id = %s) ORDER BY d.available_at, d.execution_id LIMIT 1''', (now, now, execution_id, execution_id)).fetchone()
        if row is None:
            return None
        token = token or uuid4().hex
        self.execute('UPDATE agent_dispatch SET token = %s, lease_until = %s WHERE execution_id = %s',
                     (token, now + LEASE_SECONDS, row['execution_id']))
        return Delivery(row['execution_id'], row['thread_id'], token, row['wake_version'], row['failures'])

    @access(write=True)
    def checkpoint(self, execution_id, context, status):
        row = self.execute('SELECT 1 FROM agent_dispatch WHERE execution_id = %s', (execution_id,)).fetchone()
        if row is None:
            return  # Historical/explicit foreground executions were never enrolled.
        response_id = context.get('model_response_id')
        due = time.time() + (POLL_SECONDS if status == 'waiting_model' else 0)
        if response_id and self.execute('SELECT 1 FROM agent_webhook_inbox WHERE response_id = %s AND consumed = 0',
                                        (response_id,)).fetchone():
            due = time.time()
            self.execute('UPDATE agent_webhook_inbox SET consumed = 1 WHERE response_id = %s', (response_id,))
        # Even terminal/approval checkpoints remain deliverable until publication
        # is acknowledged, so a crash between save and event append is recoverable.
        self.execute('''UPDATE agent_dispatch SET response_id = %s, available_at = %s, settled = 0
            WHERE execution_id = %s''', (response_id, due, execution_id))

    @access(write=True)
    def finish(self, delivery, *, failure=None, retry_after=0):
        row = self.execute('SELECT * FROM agent_dispatch WHERE execution_id = %s AND token = %s AND lease_until > %s',
                           (delivery.execution_id, delivery.token, time.time())).fetchone()
        if row is None:
            return False  # Stale acknowledgements cannot clear a replacement lease.
        failures = row['failures'] + (1 if failure and failure != 'execution_busy' else 0) if failure else 0
        blocked = int(failures >= MAX_FAILURES)
        due = row['available_at']
        settled = row['settled']
        if row['wake_version'] == delivery.wake_version:
            if failure:
                due = None if blocked else time.time() + (retry_after or min(60, 2 ** failures))
            else:
                status = self.execute('SELECT status FROM agent_executions WHERE execution_id = %s',
                                      (delivery.execution_id,)).fetchone()['status']
                if status in {'completed', 'failed', 'awaiting_approval'}:
                    due, settled = None, 1
                # A waiting checkpoint may already have been woken by an early webhook.
        else:
            blocked = 0  # A later durable wake wins over this acknowledgement.
        self.execute('''UPDATE agent_dispatch SET token = NULL, lease_until = 0, available_at = %s,
            failures = %s, blocked = %s, settled = %s, last_error = %s WHERE execution_id = %s''',
            (due, failures, blocked, settled, failure, delivery.execution_id))
        return bool(blocked)

    @access(write=True)
    def wake(self, thread_id, execution_id):
        row = self.execute('SELECT * FROM agent_executions WHERE execution_id = %s AND thread_id = %s',
                           (execution_id, thread_id)).fetchone()
        if row is None:
            raise ExecutionUnavailable('Unknown execution for this conversation')
        # Explicit resume may enroll an old checkpoint, but reconciliation never does.
        self.execute('''INSERT INTO agent_dispatch
            (execution_id, available_at, token, lease_until, wake_version, failures, blocked, settled, response_id, last_error)
            VALUES (%s, %s, NULL, 0, 0, 0, 0, 0, NULL, NULL) ON CONFLICT(execution_id) DO NOTHING''',
            (execution_id, time.time()))
        self.execute('''UPDATE agent_dispatch SET available_at = %s, wake_version = wake_version + 1,
            blocked = 0, failures = 0, settled = 0 WHERE execution_id = %s''', (time.time(), execution_id))

    @access(write=True)
    def notify(self, event_id, response_id, event_type):
        if self.execute('SELECT 1 FROM agent_webhook_inbox WHERE event_id = %s', (event_id,)).fetchone():
            return False
        self.execute('INSERT INTO agent_webhook_inbox VALUES (%s, %s, %s, %s, 0)',
                     (event_id, response_id, event_type, time.time()))
        self._match(response_id)
        return True

    def _match(self, response_id):
        row = self.execute('SELECT execution_id FROM agent_dispatch WHERE response_id = %s AND blocked = 0',
                           (response_id,)).fetchone()
        if row:
            self.execute('''UPDATE agent_dispatch SET available_at = %s, wake_version = wake_version + 1
                WHERE execution_id = %s''', (time.time(), row['execution_id']))
            self.execute('UPDATE agent_webhook_inbox SET consumed = 1 WHERE response_id = %s', (response_id,))

    @access(write=True)
    def reconcile(self):
        rows = self.execute('''SELECT response_id FROM agent_webhook_inbox WHERE consumed = 0
            ORDER BY received_at LIMIT 100''').fetchall()
        for row in rows:
            self._match(row['response_id'])
        # Queue rows are the outbox. This repairs a missing due time while leaving
        # approval waits, blocked deliveries and historical executions untouched.
        repaired = self.execute('''UPDATE agent_dispatch SET available_at = %s
            WHERE blocked = 0 AND settled = 0 AND available_at IS NULL''', (time.time(),)).rowcount
        self.execute('DELETE FROM agent_webhook_inbox WHERE received_at < %s', (time.time() - 7 * 86400,))
        self.execute('''INSERT INTO agent_worker_heartbeat VALUES ('dispatch', %s)
            ON CONFLICT(worker_name) DO UPDATE SET seen_at = EXCLUDED.seen_at''', (time.time(),))
        return repaired

    @access()
    def healthy(self):
        row = self.execute("SELECT seen_at FROM agent_worker_heartbeat WHERE worker_name = 'dispatch'").fetchone()
        return row is not None and row['seen_at'] > time.time() - 360
