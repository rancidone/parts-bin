---
status: stable
last_updated: 2026-04-12
---
# Design Unit: Migration To The Structured Enrichment Model

## Problem

The fallback enrichment design introduces ordered source attempts, field candidates, explicit outcomes, durable provenance, and pending review. The legacy code path returned only merged field values. Moving to the new model affects persistence, server integration, and operator-visible behavior even though the core inventory table remains flat.

Without this design, implementation would have to guess which old interfaces remain temporarily supported, whether existing rows need backfill, and how new provenance tables coexist with the current schema.

## Proposed Solution

The migration proceeds in three layers:

**1. Runtime result-shape migration** — the enrichment runtime becomes the new internal contract. Its authoritative output includes: ordered source attempts, field candidates, chosen updates, final outcome, durable provenance, and conflict details. Compatibility helpers may still expose a flattened `specs` view for legacy callers during the migration window, but that view is derivative and must not be treated as the primary contract.

**2. Persistence extension** — the existing `parts` table remains in place. Migration adds adjacent tables:

- `part_field_provenance` — accepted field-level provenance
- `part_pending_field_review` — proposed but not yet accepted field updates

Existing rows do not require backfill. Absence of provenance on older rows is a valid historical state, not a migration failure.

**3. Write-path adoption** — callers that previously wrote raw provider fields directly are updated to consume `chosen_updates` plus `durable_provenance`. Direct source writes are replaced by reconciled writes. Proposal flows use pending-review persistence. Manual acceptance writes both the field update and its provenance record together.

**Compatibility rules** — during migration, the system may still return convenience fields such as `specs`, `provider`, or `matched_part_number` for legacy call sites and logging. Those fields must remain derived from the structured result. No caller should reconstruct its own outcome logic from raw provider payloads once the new model is in place.

**Boundary** — this migration does not implement the broader flexible-part-model redesign. It is specifically a migration to richer enrichment semantics on top of the current flat `parts` schema.

## References

- `fallback-enrichment-pipeline.md` — the enrichment model this migration adopts
- `flexible-part-model-and-identity.md` — the broader schema redesign this migration explicitly does not do

## Tradeoffs

Incremental migration lowers delivery risk and preserves working inventory behavior, but leaves a temporary mix of new authoritative contracts and legacy convenience fields. Skipping historical backfill avoids fabricated provenance, but means the inventory will contain a real split between older rows with unknown provenance and newer rows with audit history.

## Readiness

High. The migration boundary, compatibility rules, schema additions, and non-backfill decision are concrete enough that implementation can move caller by caller without inventing policy.
