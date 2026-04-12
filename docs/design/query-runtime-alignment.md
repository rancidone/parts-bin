---
status: stable
last_updated: 2026-04-12
---
# Query Against The Current Inventory And Enrichment Model

## Scope

This unit defines how natural-language query should behave when inventory rows may have persisted provenance and pending field-review proposals. It covers query parsing, normalization, deterministic lookup, answer generation boundaries, and treatment of review-state data.

This unit does not redesign the enrichment runtime itself.

## Problem

The original query design assumed a simple flat inventory with no adjacent enrichment state. The current system still queries the flat `parts` table, but it now also maintains provenance records and pending review proposals that must not accidentally change query semantics.

Without this update, implementation would have to guess whether queries should read pending values, whether provenance changes filter behavior, and how answer generation relates to deterministic lookup results.

## Design Goals

- Keep query answers grounded in committed inventory rows only.
- Reuse the same canonical normalization rules used by persistence.
- Prevent pending review proposals from silently appearing as inventory facts.
- Allow the LLM to explain results without giving it authority over match selection.

## Query Structure

The query runtime should remain:

1. LLM parse into structured filters
2. deterministic filter normalization
3. exact database lookup over committed inventory rows
4. LLM answer generation over the returned result set

The enrichment attempt model does not become a new query engine. It only adds adjacent metadata that query must deliberately ignore unless a future design explicitly introduces review-aware querying.

## Authoritative Data Boundary

Query must read from committed `parts` rows only.

- `part_field_provenance` is audit data, not a primary filter table.
- `part_pending_field_review` is proposal state, not inventory state.

A field proposed by enrichment but not yet accepted must not satisfy a user query. For example, a pending manufacturer or package update cannot cause a row to match a query until that update has been accepted and written into `parts`.

## Normalization Contract

Value normalization remains shared with persistence. That shared function is still the contract that makes exact query matching reliable.

When the LLM emits filters containing `value`, query should normalize only after category context is known, using the same persistence-side rules used at write time.

## Filter Scope

The current query surface should remain limited to committed flat-schema inventory fields:

- `part_category`
- `profile`
- `value`
- `package`
- `part_number`

This is narrower than the full enrichment candidate model by design. Query should not expose source-tier, provider, provenance, or pending-review fields as implicit searchable attributes in this slice.

## Answer Generation Boundary

The LLM may summarize the matched result set for the user, but it does not decide which rows matched.

If lookup returns zero rows, the answer should reflect a definitive no-match against committed inventory, not an approximate or speculative answer based on pending or source-derived metadata.

## Future Compatibility

The presence of provenance and pending review should be treated as a reserved expansion path.

A future design may add review-aware filters or provenance inspection, but that would require explicit new user-facing semantics. It should not arrive accidentally through the existing query path.

## Tradeoffs

Ignoring pending review keeps query behavior stable and trustworthy, but it means users may not immediately find a freshly proposed package or manufacturer update until they accept it.

Keeping provenance out of the filter surface avoids accidental complexity, but it also postpones potentially useful audit-oriented queries to a later unit.

## Readiness

This unit is at high readiness. The committed-data boundary, normalization contract, and interaction with pending review are concrete enough that query implementation should not need to invent new semantics.