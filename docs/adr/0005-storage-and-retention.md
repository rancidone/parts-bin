# 0005: Keep photos ephemeral and caches separate from authoritative data

Status: Accepted

Recorded: 2026-10-10 (existing project decision)

## Context

Images and downloaded documents increase retained private data and storage
burden. Cache cleanup must not erase evidence or the records that prevent
repeated inventory effects.

## Decision

Keep photos only in browser/worker memory, outside histories, logs, caches,
durable queues, backups, and object storage. Discard retrieved documents after
extraction; retain compact passages, URLs, hashes, and retrieval/page references.
Preserve inventory, accepted evidence, reviews, conversations, approvals,
checkpoints, and mutation outcomes independently of disposable caches.

## Consequences

Interrupted initial photo analysis needs resubmission. Compact evidence cannot
reconstruct a removed source document. Cache loss can incur a new lookup, but
cannot invalidate accepted facts or retry protection. Use existing storage for
cache records; freshness, cleanup, spending allowances, and backup retention are
separate concerns. Provider retention is outside application storage policy.

[Cache contract](../../ingestion/cache.py), [local cache](../../db/enrichment_cache.py),
and [backup procedures](../operations.md#backup-and-recovery).
