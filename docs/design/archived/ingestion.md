---
status: draft
last_updated: 2026-04-12
---
# Design Unit: Ingestion Against The Enrichment Attempt Model

## Problem
The older ingestion design assumed synchronous lookup and direct spec merge before the write completed. The current system no longer works that way. A valid inventory row is written first, then eligible parts may go through asynchronous enrichment that produces source-backed proposals plus durable provenance.

Without this update, the design docs contradict the implementation on three important points:

- writes do not block on lookup
- source-backed updates are proposed, not silently merged
- pending review is part of the ingestion story

## Current Contract

Ingestion runs in four phases:

1. LLM extraction into the current flat part payload
2. deterministic completeness and duplicate handling
3. immediate write or quantity increment
4. optional background enrichment proposal for eligible rows

The LLM is responsible only for the initial extracted payload. It does not decide whether source-backed proposals are saved.

## Eligibility For Background Enrichment

Post-write enrichment runs only when the saved row:

- has a `part_number`
- has `profile = discrete_ic`
- is not one of the passive categories that use value/package identity

Passives are therefore written and deduped without treating their electrical value as a manufacturer part number.

## Write-Then-Enrich Behavior

The saved inventory row is authoritative for the ingest outcome. Background enrichment may then call the structured enrichment runtime and receive:

- ordered source attempts
- field candidates
- `chosen_updates`
- `outcome`
- `durable_provenance`

If `chosen_updates` is non-empty, those fields are persisted as pending review proposals keyed by part and field. The background step does not directly overwrite the committed row.

## Duplicate Handling

Duplicate detection still happens before enrichment:

- passives dedupe by `part_category + value + package`
- discrete/IC rows dedupe by `part_number`

If an existing row is incremented and remains enrichment-eligible, the system may still enqueue enrichment for that saved row.

## Outcome Semantics

Ingestion success is about the inventory write, not lookup completion.

- `saved`, `incomplete`, `timeout`, and `failed` enrichment outcomes do not roll back the ingest write
- `conflict` prevents automatic proposal for the conflicting fields
- no proposals is a valid result, not an ingest failure

The user-facing ingest confirmation should therefore describe the committed row or increment, not imply that enrichment has already been accepted.

## Tradeoffs

Write-then-enrich improves responsiveness and isolates inventory writes from provider latency, but it means a new part may exist briefly without enriched metadata.

Pending review is safer than silent mutation, but it creates an additional state surface that the inventory UI must expose clearly.