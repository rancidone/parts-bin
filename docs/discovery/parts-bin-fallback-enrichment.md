---
status: stable
last_updated: 2026-04-12
---
# Part Enrichment Fallback Chain

## Problem Statement

The current part-enrichment flow fails when primary distributor APIs are incomplete, sparse, mismatched, or unavailable. In those cases, the runtime has no reliable fallback chain for retrieving verifiable metadata, and the system drifts into stale or hallucinated descriptions. This causes incorrect inventory updates, misleading user-visible responses, and weak trust in lookup behavior.

## Intended Outcomes

The enrichment flow should follow a source-authority chain that preserves trust and keeps inventory updates grounded in verifiable external data.

Primary distributor APIs should be used first. If those are incomplete, the runtime should fall back to product pages and PDFs referenced by those APIs. If those sources still do not resolve the needed metadata, broader open-web search may be used only to locate a datasheet PDF, and only with explicit human confirmation.

## Source Authority

1. Highest authority: LCSC and DigiKey API responses.
2. If LCSC and DigiKey disagree: do not auto-update; prompt the user.
3. Next authority: product page scraped from a URL returned by one of those APIs.
4. Next authority: PDF linked from one of those API-derived sources.
5. Last resort: open web search to find a datasheet PDF, with human confirmation before use.

## Constraints

- Inventory must never be updated from LLM-invented lookup data.
- Automatic fallback is limited to API-derived URLs and PDFs.
- Open-web discovery is permitted only as a human-confirmed escalation.
- User-visible responses must reflect actual source outcomes, not predicted ones.
- Provenance and failure reasons must be logged clearly enough to debug bad enrichments.

## Assumptions

- LCSC and DigiKey usually agree when they both resolve the same part correctly.
- API-linked product pages and PDFs are usually sufficient to recover missing metadata.
- Disagreements between top-tier sources are rare enough to route to user confirmation.

## Risks And Edge Cases

- Distributor APIs may identify the right product but omit key metadata fields.
- Product pages and PDFs may expose different field naming or formatting.
- Source disagreement may be harder to detect than simple null or missing-field cases.
- Last-resort web search can widen the surface for bad matches if confirmation is weak.
