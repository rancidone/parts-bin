---
status: stable
last_updated: 2026-04-12
---
# Design Unit: Query Against The Current Inventory And Enrichment Model

## Problem

The original query design assumed a simple flat inventory with no adjacent enrichment state. The current system still queries the flat `parts` table but also maintains provenance records and pending review proposals that must not accidentally change query semantics.

Without this design, implementation would have to guess whether queries should read pending values, whether provenance changes filter behavior, and how answer generation relates to deterministic lookup results.

## Proposed Solution

The query runtime follows four steps:

1. LLM parse into structured filters
2. Deterministic filter normalization
3. Exact database lookup over committed `parts` rows only
4. LLM answer generation over the returned result set

**Authoritative data boundary** — query reads committed `parts` rows only. `part_field_provenance` is audit data. `part_pending_field_review` is proposal state. A field proposed by enrichment but not yet accepted must not satisfy a query — a pending manufacturer or package update cannot cause a row to match until the update has been accepted and written into `parts`.

**Normalization** — value normalization is shared with persistence. When the LLM emits filters containing `value`, normalization applies only after category context is known, using the same rules used at write time.

**Filter scope** — limited to committed flat-schema fields: `part_category`, `profile`, `value`, `package`, `part_number`. Source-tier, provider, provenance, and pending-review fields are not exposed as searchable attributes in this slice.

**Answer generation boundary** — the LLM summarizes the matched result set but does not decide which rows matched. Zero rows means a definitive no-match against committed inventory, not a speculative answer based on pending metadata.

**Future compatibility** — provenance and pending review are a reserved expansion path. Review-aware filters would require explicit new user-facing semantics and must not arrive accidentally through the existing query path.

## References

- `ingestion-enrichment-integration.md` — pending review and provenance model
- `llm-integration.md` — query parse schema and answer generation

## Tradeoffs

Ignoring pending review keeps query behavior stable and trustworthy, but users may not immediately find a freshly proposed update until they accept it. Keeping provenance out of the filter surface avoids accidental complexity but postpones audit-oriented queries to a later design unit.

## Readiness

High. The committed-data boundary, normalization contract, and interaction with pending review are concrete enough that query implementation should not need to invent new semantics.
