---
status: stable
last_updated: 2026-04-12
---
# Fallback Enrichment Pipeline

## Scope

This design unit covers the runtime path used when primary distributor APIs are incomplete, sparse, mismatched, or unavailable during part enrichment. It defines how the system resolves fallback sources, extracts candidate fields, applies source-authority rules, and decides whether inventory may be updated automatically or must pause for user confirmation.

This unit does not cover general chat UX, long-term session memory, or broad open-web ranking strategy beyond the explicit confirmed-search escalation boundary.

## Design Goals

- Keep inventory updates grounded in verifiable external source data.
- Preserve a deterministic source-authority chain.
- Prevent LLM-generated lookup text from contaminating stored metadata.
- Make source provenance and failure reasons inspectable in logs.
- Allow automatic fallback only within bounded, reviewable source classes.

## Proposed Structure

The enrichment runtime should produce a per-request enrichment attempt record that moves through ordered stages:

1. Primary API resolution.
2. API disagreement check.
3. API-derived product page fetch and extraction.
4. API-derived PDF fetch and extraction.
5. Human-confirmed open-web datasheet search.
6. Field-level reconciliation and update decision.

Each stage produces structured evidence rather than directly mutating inventory. Inventory mutation happens only after reconciliation decides that the resulting fields are trustworthy enough for automatic update.

## Core Components

### Source Resolution

The runtime resolves candidate sources in authority order.

Primary sources are LCSC and DigiKey API responses. Each response is normalized into a source record with:

- source kind
- authority tier
- resolved identity fields
- source URL when available
- extracted candidate fields
- fetch status
- diagnostic metadata

If both primary APIs resolve and they disagree on core identifying fields, the runtime stops automatic enrichment and surfaces a user prompt.

### Page Extraction

If API fields are missing but an API response includes a product page URL, the runtime fetches that page and extracts structured metadata from the returned HTML.

Page extraction is bounded to API-derived URLs only. It should prefer embedded structured data, machine-readable metadata, and stable product-detail sections before falling back to looser text extraction.

### PDF Extraction

If page extraction still leaves required fields unresolved and an API-derived page or API payload references a PDF, the runtime fetches that PDF and extracts candidate fields from it.

PDF extraction is intended as a metadata recovery step, not a general document-ingestion pipeline. The output is limited to the inventory-relevant field set already used by enrichment.

### Confirmed Search Escalation

If API, page, and PDF stages still do not provide enough trustworthy data, the runtime may prepare a last-resort open-web search for a datasheet PDF. That stage is never automatic. The user must confirm the candidate before the system uses it as a source.

### Reconciliation And Update Decision

All extracted values are accumulated as field candidates with provenance. The reconciler determines whether a field may be written automatically, must be withheld, or requires user confirmation.

## Field Authority Rules

Authority is applied per field, not just per request.

- LCSC and DigiKey API fields are highest authority.
- API-derived page fields may fill gaps left by API responses.
- API-derived PDF fields may fill gaps still unresolved after page extraction.
- Confirmed open-web PDF fields may be used only after explicit user confirmation.
- LLM-generated descriptions or summaries are never eligible for persistence as raw source facts.

If the highest-authority sources disagree on a field, that field is not auto-updated. The runtime prompts the user with the conflicting values and source provenance.

If a lower-tier source conflicts with a higher-tier source, the higher-tier source wins and the lower-tier value is logged but not persisted.

Blocking disagreement between LCSC and DigiKey is defined as disagreement on any of these identifying fields:

- `part_number`
- `package`
- `manufacturer`
- categorization (`part_category` / `profile`)

When either API resolves a value for one of those fields and the other API resolves a conflicting value, the runtime must stop automatic enrichment and surface a conflict outcome for user confirmation.

## Fallback Field Scope

The current schema supports these enrichment-relevant fields:

- `manufacturer`
- `part_number` as the canonical manufacturer part number
- `package`
- `description` as the detailed textual description
- `part_category`
- `profile`
- `value`

For this design, automatic fallback enrichment should treat the writable target field set as:

- manufacturer
- canonical part number
- package
- detailed description
- categorization (`part_category` and `profile` together)
- value

`quantity` is out of scope for fallback enrichment because it is not source metadata.

Categorization requires special handling because external sources may describe a part richly without mapping cleanly onto the current local taxonomy. When extraction cannot confidently assign both `part_category` and `profile`, the runtime should withhold categorization updates rather than forcing a guess.

`value` is in scope, but only when a source exposes it in a form that can be normalized into the local canonical representation. Freeform descriptive text that merely implies value should not be auto-persisted as `value` without deterministic normalization.

## Description Merge Policy

Description is treated differently from the identifying fields.

If manufacturer, canonical part number, package, and categorization agree strongly enough across the highest-authority sources, the runtime may use an LLM as a normalization layer to merge source descriptions into one canonical detailed description for storage.

That merge step is allowed only after identity agreement has been established. The LLM is not acting as a source of facts. It is acting as a reducer over already accepted source text.

The merge input must be limited to verified source descriptions gathered from APIs, API-derived pages, and API-derived PDFs. The stored description should therefore be attributable to source-backed content even if the final wording is normalized.

If the identifying fields do not agree, the description must not be merged automatically. The runtime should instead surface a conflict and wait for user confirmation.

## Provenance Persistence

Provenance should be stored alongside any persisted enrichment data, not only in runtime artifacts or logs.

Each persisted field update should have a durable provenance companion record that makes it possible to answer:

- which source produced the winning value
- which authority tier it came from
- which source locator was used
- how the value was extracted
- whether there were competing candidates
- whether the stored value was copied directly or normalized through a reducer step

For merged descriptions, the durable provenance record must preserve the set of source descriptions that were merged and identify the merge step as a normalization operation rather than a primary fact source.

This implies a storage addition beyond the current flat `parts` table. The design does not fix the exact schema here, but it requires durable provenance linkage for every field written by enrichment.

## Required Provenance Model

Each candidate field should carry:

- field name
- candidate value
- source tier
- source kind
- source locator
- extraction method
- confidence marker
- conflict status

Persisted updates should be traceable back to the winning candidate values so debugging does not depend on reconstructing logs from conversation text.

For merged descriptions, provenance must also record the set of source descriptions that were merged and whether the final stored value was copied directly or normalized through the LLM reducer.

## Runtime Outcomes

The runtime should distinguish at least these outcomes:

- `saved`: one or more fields were updated from verifiable sources
- `no_match`: no acceptable source produced matching metadata
- `incomplete`: source resolution succeeded but no new writable fields were recovered
- `conflict`: high-authority sources disagree and user input is required
- `timeout`: a bounded source fetch timed out before completion
- `failed`: the attempt terminated due to an operational error
- `needs_confirmation`: last-resort open-web escalation found a candidate that requires user confirmation

User-visible responses should be generated from these outcomes, not from LLM-predicted success text.

## Interfaces

The current lookup helper is too narrow because it returns only merged field values. The fallback pipeline needs a richer result shape.

The runtime-facing enrichment result should include:

- request part number and inventory id
- ordered source attempts
- field candidates by field name
- chosen updates
- final outcome code
- whether user confirmation is required
- user-visible status payload
- durable provenance payload for each persisted field

The storage layer should continue receiving only concrete field updates, but the enrichment layer should retain enough detail to explain why each update was or was not written.

## Logging And Diagnostics

Logs should record:

- each source attempt
- fetch status and latency
- resolved URLs
- extracted identifying fields
- field conflicts
- timeout/failure classification
- final reconciliation outcome

Debug logging may include trimmed raw payload summaries for provider APIs and parsed page/PDF metadata, but ordinary logs should remain compact.

## Tradeoffs

This design intentionally adds more runtime structure and provenance tracking than the current lookup helper. That increases complexity, but it is necessary to separate trustworthy source-derived data from model-generated conversation text.

The design also prefers bounded source classes over unrestricted automatic search. That reduces coverage, but it keeps automatic updates auditable and lowers mismatch risk.

Allowing LLM-based description merging adds some semantic normalization risk, but constraining it to already-agreed identity fields and verified source text keeps that risk bounded and substantially lower than using the LLM as a source generator.

Durable provenance storage increases schema and write complexity, but it removes ambiguity about where stored enrichment data came from and makes later correction or audit materially easier.

## Open Questions

None at this unit level.

## Readiness

This unit is design-ready at high readiness. The fallback chain, authority rules, conflict criteria, outcome model, writable field set, description-merge policy, and provenance requirement are now concrete enough that implementation should not need to invent core behavior.