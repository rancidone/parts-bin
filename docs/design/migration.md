# Design Unit: Migration To The Structured Enrichment Model

## Problem

The older lookup flow returned only merged spec fields. The current enrichment model returns a structured result with ordered source attempts, field candidates, explicit outcomes, and durable provenance. That change affects runtime contracts, persistence, server behavior, and UI review flows even though the main `parts` table is still flat.

## Migration Goal

Move callers to the structured enrichment model without forcing a full inventory-schema rewrite.

## Migration Strategy

The migration happens in three layers:

1. runtime result-shape migration
2. persistence schema extension
3. caller-by-caller adoption

### 1. Runtime Result Shape

The authoritative enrichment output is now the structured attempt model:

- ordered source attempts
- field candidates
- `chosen_updates`
- `outcome`
- `durable_provenance`
- conflict details

Flattened convenience fields such as `specs` may still exist temporarily for compatibility, but they are derived from the structured result and are not the primary contract.

### 2. Persistence Extension

The `parts` table remains the committed inventory record. Migration adds adjacent tables:

- `part_field_provenance` for accepted field-level provenance
- `part_pending_field_review` for proposed but not yet accepted updates

Historical rows do not need synthetic backfill. Missing provenance on older rows is a valid historical state.

### 3. Caller Adoption

Callers that used to write raw provider fields directly should instead consume:

- `chosen_updates` for actual proposed field changes
- `durable_provenance` for accepted writes and review flows

That means:

- direct source writes are replaced by reconciled writes
- background enrichments persist proposals instead of silent field mutation
- manual acceptance writes both field values and provenance together

## Boundaries

This migration does not implement the broader flexible-part-model redesign. It is specifically a migration to richer enrichment semantics on top of the current flat `parts` schema.

## Tradeoffs

Incremental migration lowers delivery risk and preserves working inventory behavior, but it leaves a temporary mix of new authoritative contracts and legacy convenience fields.

Skipping historical backfill avoids fabricated provenance, but the inventory will contain a real split between older rows with unknown provenance and newer rows with audit history.
