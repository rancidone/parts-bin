# Deduplicate bulk creation in the domain transaction

## Status

Accepted; supersedes bulk duplicate rejection in ADR 0045.

## Context

ADR 0045 required an inventory check before bulk creation because an existing
identity rejected the batch. That adds a model round and leaves a gap between
checking stock and committing new records.

## Decision

Supersede ADR 0045's duplicate rejection for bulk creation. The typed domain
operation validates every row, then creates missing identities and skips existing
or repeated input identities in one transaction. Use the existing normalized
identity rules: full ordering codes, or passive category/value/package. Unknown
non-passive groups remain separate. For repeated identities, the first input
wins; later quantities are not summed. Return an input-indexed outcome and the
committed part for every row, distinguishing existing stock from repeated input.

Skipped records retain quantity, metadata, timestamps and evidence, including
zero-stock records. Invalid rows or ambiguous historical identities roll back
the entire batch. Preserve operation receipts in that transaction so delivery
replays return the original outcomes. Explicit additional physical stock still
uses stock increments at identified records through the shared policy.

Agent instructions submit confirmed additions directly to `add_parts` and report
its outcomes, using a one-item array for a single part. This includes identified
ICs; supplier enrichment is a separate, explicitly requested action. Availability
checks remain separate read-only operations when requested by the user. Bulk creation performs no implicit enrichment.

## Consequences

A confirmed batch requires one tool call and a final model summary. Duplicate
handling is independent of model interpretation and concurrent writes are
serialized by the existing repository transaction boundary. This changes the
bulk creation result to include per-row outcomes and added/skipped counts; it
does not merge historical records or change single-part creation semantics.
