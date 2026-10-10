# 0003: Commit mutation outcomes with effects and recover explicitly

Status: Accepted

Recorded: 2026-10-10 (existing project decision)

## Context

A connection or worker can fail after an inventory mutation but before the
user sees its result. Transcript replay cannot establish whether work completed,
and retrying uncertain provider requests can incur another charge.

## Decision

Commit each inventory effect and saved outcome in one transaction under a
stable operation ID. Bind approvals to target snapshots and revalidate before
applying them. Persist execution context and tool queues separately from visible
events; deduplicate publication and replay. Fence workers with leases. Require
explicit resume, and report interrupted retrieval instead of blindly repeating it.

## Consequences

Duplicate mutation delivery can return its original outcome. Provider calls stay
outside inventory transactions. Changed targets require fresh approval. An
uncheckpointed provider response remains uncertain; explicit resume may repeat a
paid call. Leases provide ownership, not scheduling or exactly-once billing.

[Execution](../../db/execution.py), [approval](../../agent_runtime/approval.py),
[conversation adapter](../../db/conversations.py), and
[recovery procedures](../operations.md#interrupted-requests).
