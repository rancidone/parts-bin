# Migration To The Structured Enrichment Model

## Scope

This unit defines the migration boundary from the older flat lookup helper to the structured enrichment attempt model. It covers runtime compatibility, schema additions, write-path migration, and the limits of this migration slice.

It does not redesign the entire inventory schema. The existing `parts` table shape remains the primary storage record in this slice.

## Problem

The fallback enrichment design introduces ordered source attempts, field candidates, explicit outcomes, durable provenance, and pending review. The legacy code path returned only merged field values. Moving to the new model affects persistence, server integration, and operator-visible behavior even though the core inventory table remains flat.

Without a migration design, implementation would have to guess which old interfaces remain temporarily supported, whether existing rows need backfill, and how new provenance tables coexist with the current schema.

## Design Goals

- Introduce the structured enrichment result without forcing a full inventory-schema rewrite.
- Preserve compatibility where older call sites still expect flat spec fields during the migration window.
- Add durable provenance and pending review storage incrementally.
- Keep existing inventory rows valid without mandatory historical backfill.

## Migration Strategy

The migration should happen in three layers:

1. runtime result-shape migration
2. persistence schema extension
3. caller-by-caller server and UI adoption

### Runtime Result-Shape Migration

The enrichment runtime becomes the new internal contract.

Its authoritative output includes:

- ordered source attempts
n- field candidates
- chosen updates
- final outcome
- durable provenance
- conflict details

Compatibility helpers may still expose a flattened `specs` view for legacy callers during the migration window, but that flattened view is derivative and should not be treated as the primary contract.

### Persistence Extension

The existing `parts` table remains in place.

Migration adds adjacent tables rather than replacing the core inventory record:

- `part_field_provenance` for accepted field-level provenance
- `part_pending_field_review` for proposed but not yet accepted field updates

Existing rows do not require backfill into these tables. Absence of provenance on older rows is a valid historical state, not a migration failure.

### Write-Path Adoption

Callers that previously wrote raw provider fields directly should be updated to consume `chosen_updates` plus `durable_provenance`.

This means:

- direct source writes are replaced by reconciled writes
- proposal flows use pending-review persistence
- manual acceptance writes both the field update and its provenance record together

## Compatibility Rules

During migration, the system may still return convenience fields such as `specs`, `provider`, or `matched_part_number` for legacy call sites and logging.

Those compatibility fields must remain derived from the structured result. No caller should reconstruct its own outcome logic from raw provider payloads once the new model is in place.

## Non-Backfilled Historical Data

The migration does not require retroactively inventing provenance for pre-migration rows.

Historical rows without provenance should continue to function normally in query, export, and edit flows. Provenance begins at the point a field is first written or overwritten through the new enrichment-aware path.

## Risks And Boundaries

The biggest migration risk is semantic drift between the new structured result and any temporary compatibility views. That risk is acceptable only if the compatibility surface stays thin and clearly marked as transitional.

A second risk is overreaching into the broader flexible-part-model redesign. This migration should not do that. It is specifically a migration to richer enrichment semantics on top of the current flat inventory schema.

## Tradeoffs

Incremental migration lowers delivery risk and preserves working inventory behavior, but it leaves the system with a temporary mix of new authoritative contracts and legacy convenience fields.

Skipping historical backfill avoids fabricated provenance, but it means the inventory will contain a real split between older rows with unknown provenance and newer rows with audit history.

## Readiness

This unit is at high readiness. The migration boundary, compatibility rules, schema additions, and non-backfill decision are concrete enough that implementation can move caller by caller without inventing policy.