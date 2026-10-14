# 0042: Apply candidate text search to Inventory suggestions

**Status:** Accepted; supersedes the text-query exclusion in [0041](0041-cross-filtered-inventory-suggestions.md).

## Context

Suggestions that ignore text can offer values or packages absent from the
matching candidates. The domain already defines literal multi-term matching
across committed inventory fields.

## Decision

Pass the bookmark’s text query to the read-only suggestion operation. For each
list, use shared candidate search when the query contains terms, retaining the
existing structured cross-filters and excluding the list’s own filter. Blank
text uses shared structured search. Paging remains excluded.

Reuse candidate rules and query bounds; do not interpret text as units or
implement term matching in the UI. Unit-aware matching remains specific to the
structured nominal-value filter. Preserve stored suggestion spelling, current
inputs, and existing bookmark parameters.

## Consequences

Both lists now reflect text, category, per-record stock and the other structured
filter. Pending text proposals remain excluded. Text changes invalidate options
and obsolete responses just like structured filter changes. No new persistence,
provider calls or inventory mutations are required.
