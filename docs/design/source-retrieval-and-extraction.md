---
status: stable
last_updated: 2026-04-12
---
# Design Unit: Source Retrieval And Extraction

## Problem

The fallback pipeline depends on product pages and PDFs as bounded source classes, but the system has no design for how those artifacts are fetched, parsed, normalized, and turned into field candidates. Without this design, implementation would have to guess at extraction boundaries, site-specific handling, and failure semantics.

## Proposed Solution

The source-extraction subsystem splits into four stages:

1. Retrieval
2. Content classification
3. Extractor selection
4. Field candidate normalization

The output is not a direct inventory update — it is a set of candidate fields plus extraction provenance that the enrichment reconciler evaluates.

**Retrieval boundaries** — automatic retrieval is allowed only for product page URLs returned by LCSC or DigiKey APIs, and PDF URLs returned by those APIs or discovered from their API-derived pages. Each retrieval captures: requested URL, final resolved URL, content type, HTTP status, timing, redirect chain, and failure classification. If retrieval would leave the trusted-source boundary, the attempt fails closed unless the broader search flow has already been user-confirmed.

**Content classification** — fetched artifacts are classified as: structured HTML product page, PDF document, unsupported content, or retrieval failure. Classification is based on response headers plus lightweight content inspection, not URL suffix alone.

**Extractor selection** — layered:

1. Dedicated provider extractors for DigiKey and LCSC
2. Generic structured-data extraction for HTML (JSON-LD, table structures, stable labeled sections)
3. Generic PDF text extraction for source-backed datasheets

The system prefers a narrower provider extractor over a broader generic one whenever both are available.

**Provider extractor strategy** — dedicated DigiKey and LCSC extractors use a layered parsing strategy: (1) known structured fields and embedded metadata, (2) stable labeled sections or tables, (3) bounded fuzzy field matching over nearby labels and values. The fuzzy layer absorbs minor DOM and wording drift but stays limited to the known target field set and known page regions — it does not infer new fields from unrelated free text.

**HTML extraction order** — embedded structured data → stable labeled product detail sections → provider-specific parsers → bounded fuzzy label-to-field matching → generic structured fallback. For each extracted value, the extractor emits both the candidate value and the local page evidence used to derive it.

**Fuzzy matching constraints** — fuzzy logic is allowed only inside a bounded parser context: matching must stay within provider-specific trusted page regions, candidate fields must come from the known enrichment schema only, and low-confidence fuzzy matches must be surfaced as partial or ambiguous extraction rather than silently persisted.

**PDF extraction** — treats the document as a fallback metadata source, not a full technical document parser. First pass attempts deterministic extraction from the first pages and obvious part-summary sections. Emits: candidate fields, page references, extraction snippets, and confidence markers. If the PDF contains multiple ordering variants or family-level listings, extraction must not guess the exact variant without explicit supporting evidence.

**Output contract** — the extraction subsystem returns: source locator, source kind, extractor used, extracted candidate fields, evidence handles per candidate, extraction warnings, and extraction status. A successful extraction may still be incomplete; completeness and update eligibility are separate decisions.

**Failure semantics** — distinguished states: retrieval timeout, retrieval denied or blocked, unsupported content type, extractor not available, extractor produced no candidates, extractor produced ambiguous candidates, extractor produced partial candidates. These states flow upward intact rather than being collapsed into a single generic lookup failure.

## References

- `fallback-enrichment-pipeline.md` — source authority order, conflict policy, and reconciliation

## Tradeoffs

Dedicated DigiKey and LCSC extractors improve reliability for the most important sources but create source-specific maintenance work. Layering structured parsing with bounded fuzzy matching reduces brittleness versus exact-selector scraping, but requires explicit confidence handling so weak matches do not silently become stored metadata. PDF extraction can recover fields absent from product pages but introduces more ambiguity and weaker structure than HTML extraction.

## Readiness

High. The retrieval boundary, parser strategy, extractor layering, and failure model are concrete enough for implementation.

Open question: what raw evidence should be stored durably versus referenced indirectly in provenance records?
