---
status: draft
last_updated: 2026-04-12
---
# Design Unit: Query Against Committed Inventory

## Problem
The original query design assumed a simple flat inventory with no adjacent enrichment state. The current system still queries committed `parts` rows, but it also maintains accepted provenance and pending field-review proposals. Query semantics need to be explicit so pending or source-derived metadata does not accidentally become live inventory.

## Flow

```
natural language query
  └─ LLM parsing → structured filters
       └─ normalization using the same persistence-side rules as writes
            └─ exact DB lookup over committed `parts` rows
                 └─ LLM answer generation over the matched rows
```

## Authoritative Data Boundary

Ordinary query reads committed inventory rows only.

- `parts` is the source of truth for matching
- `part_field_provenance` is audit metadata
- `part_pending_field_review` is proposal state

A proposed field change does not affect query results until the user accepts it and the committed row is updated.

## Normalization Contract

Value normalization remains shared with persistence. That shared function is still the contract that keeps write-time and query-time matching aligned.

When query filters include `value`, normalization should happen only after category context is known.

## Filter Scope

The current query surface remains intentionally narrow:

- `part_category`
- `profile`
- `value`
- `package`
- `part_number`

Query does not currently expose provenance, provider, authority tier, or pending-review fields as searchable attributes.

## Answer Generation Boundary

The LLM may summarize the matched rows for the user, but it does not decide which rows matched.

- zero rows means a definitive no-match against committed inventory
- no fuzzy matching
- no speculative use of pending review data

## Tradeoffs

Ignoring pending review keeps query trustworthy, but users will not find a freshly proposed package or manufacturer change until they accept it.

Keeping provenance out of the query surface avoids accidental complexity, but it postpones audit-oriented queries to a later design unit.