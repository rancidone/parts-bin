---
status: draft
last_updated: 2026-04-12
---
# Design Unit: Fallback Enrichment Pipeline

## Problem

The current part-enrichment flow fails when primary distributor APIs are incomplete, sparse, mismatched, or unavailable. The runtime has no reliable fallback chain for retrieving verifiable metadata, which causes incorrect inventory updates, misleading user-visible responses, and weak trust in lookup behavior.

## Proposed Solution

The enrichment runtime produces a per-request attempt record that moves through ordered stages:

1. Primary source resolution (JLC parts local catalog and DigiKey API)
2. API disagreement check
3. API-derived product page fetch and extraction
4. API-derived PDF fetch and extraction
5. Human-confirmed open-web datasheet search
6. Field-level reconciliation and update decision

Each stage produces structured evidence. Inventory mutation happens only after reconciliation decides the resulting fields are trustworthy enough for automatic update.

**Source resolution** — primary sources are the JLC parts local catalog (authority tier: `local_db`) and the DigiKey API (authority tier: `primary_api`), each normalized into a source record with: source kind, authority tier, resolved identity fields, source URL, extracted candidate fields, fetch status, and diagnostic metadata. If both sources resolve and disagree on core identifying fields (`part_number`, `package`, `manufacturer`, or categorization), the runtime stops automatic enrichment and surfaces a conflict outcome for user confirmation.

**Page extraction** — if API fields are missing but an API response includes a product page URL, the runtime fetches that page and extracts structured metadata from HTML. Bounded to API-derived URLs only.

**PDF extraction** — if page extraction still leaves required fields unresolved and an API-derived source references a PDF, the runtime fetches that PDF and extracts candidate fields. Limited to the inventory-relevant field set.

**Confirmed search escalation** — if all prior stages yield `no_match` or `incomplete`, the runtime performs an open-web datasheet search (Brave Search API) and fetches the first resolvable PDF result. Extraction runs immediately. The resulting field candidates are stored as `authority_tier=web_search` and surfaced as pending field review rather than auto-applied. The outcome is `needs_confirmation`. The user confirms or rejects through the existing pending-review flow (`/inventory/{id}/accept` or `/inventory/{id}/dismiss`). This stage requires a `[search]` config section with an `api_key`; if absent, the stage is skipped.

**Field authority rules** — authority is applied per field:

- JLC parts local catalog and DigiKey API fields are highest authority
- API-derived page fields may fill gaps left by API responses
- API-derived PDF fields may fill remaining gaps
- Web-search PDF fields are surfaced as pending review (`needs_confirmation`) and require user acceptance before being written
- LLM-generated descriptions are never eligible for persistence as raw source facts

If highest-authority sources disagree on a field, that field is not auto-updated. Lower-tier conflicts with higher-tier sources are logged but not persisted.

**Writable field set** — `manufacturer`, `part_number`, `package`, `description`, `part_category`, `profile`, `value`. `quantity` is excluded — it is not source metadata. Categorization is withheld when extraction cannot confidently assign both `part_category` and `profile`. `value` is only written when it can be normalized into canonical form.

**Description merge policy** — if identifying fields (manufacturer, part number, package, categorization) agree across highest-authority sources, the runtime may use the LLM as a normalization reducer over the verified source descriptions to produce one canonical description. The LLM is acting as a reducer, not a fact source. If identifying fields do not agree, description must not be merged automatically.

**Provenance persistence** — every persisted field update must have a durable provenance record answering: which source produced the winning value, its authority tier and locator, the extraction method, whether there were competing candidates, and whether the value was copied directly or normalized. For merged descriptions, the source descriptions used in the merge must be recorded.

**Runtime outcomes**: `saved`, `no_match`, `incomplete`, `conflict`, `timeout`, `failed`, `needs_confirmation`. User-visible responses are generated from these outcomes, not from LLM-predicted success text.

**Result shape** — the enrichment result returned to callers must include: ordered source attempts, field candidates by field name, chosen updates, final outcome code, confirmation requirement flag, and durable provenance payload for each persisted field.

## References

- `source-retrieval-and-extraction.md` — page and PDF fetch and parse mechanics
- `ingestion-enrichment-integration.md` — how ingestion consumes enrichment results

## Tradeoffs

More runtime structure and provenance tracking than the current lookup helper increases complexity, but it is necessary to separate trustworthy source-derived data from model-generated conversation text. Bounded source classes reduce coverage but keep automatic updates auditable. LLM-based description merging adds some semantic normalization risk, but constraining it to already-agreed identity fields and verified source text keeps that risk low. Durable provenance storage increases schema and write complexity but removes ambiguity about where enrichment data came from.

## Readiness

Fully implemented.

- **Description merge** — `fetch_specs_detailed` accepts an optional `llm` parameter; when two or more deduplicated description candidates exist and identity fields are not in conflict, `LLMClient.merge_descriptions()` reduces them into one canonical description. Provenance records the source descriptions and marks `normalization_method="llm_description_merge"`.
- **Confirmed search escalation** — `fetch_specs_detailed` accepts an optional `search_config` dict. When all prior stages yield `no_match` or `incomplete` and a Brave Search `api_key` is configured, the pipeline searches for a datasheet PDF, extracts it immediately as `authority_tier=web_search`, and returns `outcome=needs_confirmation` with the candidates stored as pending review. Skipped if `[search]` config is absent.
