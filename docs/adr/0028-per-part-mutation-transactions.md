# 0028: Commit mutations and bulk outcomes per part

Status: Accepted

Recorded: 2026-10-13

## Context

The application primarily needs to find and manage personal stock. Cross-part
atomic bulk edits and nested savepoint recovery add storage requirements without
an essential user benefit. Retry safety and approval validation remain necessary.

## Decision

Keep each single-part effect and its receipt atomic, as in [0003](0003-durable-effects-and-explicit-recovery.md).
Apply bulk edits as independent, snapshot-bound child approvals with stable IDs.
Persist successes and failures per part; save the aggregate outcome after all
children. Present partial success explicitly and require fresh review for failures.
Roll back a failed single-part operation fully before saving its rejection. Retry
only complete database-only units after serialization conflicts. Provider calls
remain outside transactions. Remove savepoints and cross-part atomicity requirements.

## Consequences

Worker replacement can replay child receipts without repeating successful effects.
A user can see some parts saved while others conflict. New uniqueness claims still
receive server validation. Approval checks, worker fencing and receipt writes share
a transaction. The adapter serializes write units through one installation revision
row; this favors correctness and simplicity at low write volume, with contention
and backoff to measure before larger workloads.
