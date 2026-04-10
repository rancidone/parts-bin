# Ingestion Against The Enrichment Attempt Model

## Scope

This unit defines how ingestion uses the structured enrichment attempt model after the fallback enrichment design is in place. It covers new-part writes, duplicate handling, asynchronous enrichment, pending review persistence, and the boundary between conversational extraction and source-backed field updates.

This unit does not redesign the extraction prompt or the fallback source-authority chain. Those are inherited from the LLM and fallback-enrichment units.

## Problem

The legacy ingestion design assumed that discrete and IC parts were synchronously enriched before the write completed. The current model no longer works that way. Ingestion now has to commit a valid inventory row first, then run enrichment as a separate source-backed proposal flow with durable provenance and optional user review.

Without this update, implementation would have to guess whether ingestion blocks on lookup, whether source-derived fields may overwrite extracted fields directly, and how pending enrichment proposals attach to an already-written part.

## Design Goals

- Keep part creation deterministic even when lookup is slow or unavailable.
- Separate conversational extraction from source-backed field mutation.
- Reuse the same enrichment runtime for new-part ingestion and later manual refresh.
- Preserve user-entered quantity and category decisions even when enrichment proposes metadata changes.
- Make post-write enrichment reviewable instead of silently mutating inventory.

## Ingestion Structure

The ingestion runtime should proceed in four phases:

1. conversational extraction into the existing flat part payload
2. deterministic completeness and duplicate handling
3. immediate inventory write or quantity increment
4. optional asynchronous enrichment proposal for eligible rows

The LLM extraction step remains responsible only for producing the initial part payload. It does not decide whether any source-backed proposal should be committed.

## Eligibility For Post-Write Enrichment

Automatic follow-on enrichment should run only when the saved part:

- has a `part_number`
- is `profile = discrete_ic`
- is not one of the passive categories that rely on value/package identity

This keeps enrichment aligned with the current schema and avoids treating passive values as manufacturer part numbers.

## Write-Then-Enrich Contract

For eligible parts, ingestion must write the row before starting enrichment.

That write establishes the canonical inventory identity. The subsequent enrichment attempt may propose updates for writable metadata fields such as:

- `manufacturer`
- `part_number`
- `package`
- `description`

The enrichment attempt must not directly overwrite the row during the background phase. Instead it persists proposed field changes into pending review storage together with durable provenance records derived from the winning candidates.

## Pending Review Model

Post-write enrichment is a proposal flow, not an automatic save.

When the enrichment runtime returns `chosen_updates`, ingestion should persist those fields into `part_pending_field_review` keyed by part id and field name. Each proposed field must retain its provenance payload so later acceptance can write both the chosen value and its durable provenance record together.

If the enrichment runtime returns no chosen updates, ingestion should leave the saved row unchanged and produce no pending review record.

## Duplicate Handling

Duplicate detection still happens before enrichment.

If ingestion resolves to an existing row and increments quantity, the system may still enqueue enrichment for that row when the resulting saved record is enrichment-eligible. This avoids permanently coupling enrichment only to first insert while still keeping quantity updates independent from source fetch success.

## Failure And Outcome Handling

Ingestion should treat enrichment outcomes as secondary status, not write-blocking status.

- `saved`, `incomplete`, `timeout`, and `failed` do not roll back the inventory write.
- `conflict` should prevent automatic field proposal for the conflicting fields and should surface as a reviewable outcome when relevant.
- absence of proposals is not an ingestion failure; it means the row remains as entered.

User-visible confirmation for the initial ingest should therefore describe the committed row, not imply that all enrichment finished successfully.

## Interfaces

The ingestion-to-enrichment boundary should pass:

- persisted `part_id`
- canonical `part_number`
- optional provider credentials / local catalog configuration

The enrichment result consumed by ingestion should include at least:

- `chosen_updates`
- `durable_provenance`
- `outcome`
- provider diagnostics for logs

## Tradeoffs

Write-then-enrich improves responsiveness and avoids tying inventory mutation to flaky provider latency, but it means newly added parts may temporarily exist without enriched metadata.

Pending review is safer than silent background mutation, but it introduces another state surface that the UI and persistence layers must expose coherently.

## Readiness

This unit is at high readiness. The ingestion boundary, eligibility rules, proposal flow, and failure semantics are concrete enough for implementation and match the runtime shape already adopted by the structured enrichment model.