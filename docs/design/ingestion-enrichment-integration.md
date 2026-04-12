---
status: stable
last_updated: 2026-04-12
---
# Design Unit: Ingestion Against The Enrichment Attempt Model

## Problem

The legacy ingestion design assumed discrete and IC parts were synchronously enriched before the write completed. The current model no longer works that way. Ingestion must commit a valid inventory row first, then run enrichment as a separate source-backed proposal flow with durable provenance and optional user review.

Without this design, implementation would have to guess whether ingestion blocks on lookup, whether source-derived fields may overwrite extracted fields directly, and how pending enrichment proposals attach to an already-written part.

## Proposed Solution

Ingestion proceeds in four phases:

1. Conversational extraction into the flat part payload
2. Deterministic completeness and duplicate handling
3. Immediate inventory write or quantity increment
4. Optional asynchronous enrichment proposal for eligible rows

The LLM is responsible only for producing the initial extracted payload. It does not decide whether any source-backed proposal is committed.

**Eligibility for post-write enrichment** — automatic follow-on enrichment runs only when the saved row has a `part_number`, is `profile = discrete_ic`, and is not a passive category. This avoids treating passive values as manufacturer part numbers.

**Write-then-enrich contract** — for eligible parts, the row is written before enrichment starts. The write establishes canonical inventory identity. The enrichment attempt may propose updates to writable metadata fields (`manufacturer`, `part_number`, `package`, `description`) but must not directly overwrite the row during the background phase. Proposed field changes are persisted into `part_pending_field_review` keyed by part id and field name, together with durable provenance records from the winning candidates.

**Duplicate handling** — duplicate detection happens before enrichment. If ingestion resolves to an existing row and increments quantity, the system may still enqueue enrichment for that row when it is enrichment-eligible.

**Failure and outcome handling** — enrichment outcomes are secondary, not write-blocking. `saved`, `incomplete`, `timeout`, and `failed` do not roll back the inventory write. `conflict` prevents automatic field proposal for the conflicting fields. Absence of proposals is not an ingestion failure. User-visible confirmation describes the committed row, not enrichment status.

**Interfaces** — the ingestion-to-enrichment boundary passes: persisted `part_id`, canonical `part_number`, and optional provider credentials. The enrichment result consumed by ingestion must include `chosen_updates`, `durable_provenance`, `outcome`, and provider diagnostics for logs.

## References

- `fallback-enrichment-pipeline.md` — enrichment source-authority chain
- `llm-integration.md` — extraction prompt and schema

## Tradeoffs

Write-then-enrich improves responsiveness and avoids tying inventory mutation to flaky provider latency, but newly added parts may temporarily exist without enriched metadata. Pending review is safer than silent background mutation, but it introduces a state surface that the UI and persistence layers must expose coherently.

## Readiness

High. The ingestion boundary, eligibility rules, proposal flow, and failure semantics are concrete enough for implementation and match the runtime shape already adopted by the structured enrichment model.
