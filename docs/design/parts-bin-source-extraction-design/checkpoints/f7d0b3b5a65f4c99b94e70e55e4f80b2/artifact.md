# Source Retrieval And Extraction

## Scope

This design unit covers how the fallback enrichment pipeline fetches and extracts metadata from API-derived product pages and PDFs. It defines retrieval boundaries, extraction strategy, parser layering, source-specific handling, and failure behavior.

This unit does not redefine source authority order or conflict policy. Those are inherited from the parent fallback-enrichment design.

## Problem

The fallback pipeline depends on product pages and PDFs as bounded source classes, but the system currently has no design for how those artifacts are fetched, parsed, normalized, and turned into field candidates. Without that design, implementation would have to guess at extraction boundaries, site-specific handling, and failure semantics.

## Design Goals

- Keep retrieval bounded to trusted source URLs unless the user explicitly confirms escalation.
- Prefer deterministic extraction over generic heuristic scraping.
- Make extraction failures diagnosable without treating partial parses as successful enrichment.
- Support both structured HTML product pages and PDFs within one coherent extraction interface.
- Preserve enough raw context to support provenance and later debugging.

## Proposed Structure

The source-extraction subsystem should split into four stages:

1. retrieval
2. content classification
3. extractor selection
4. field candidate normalization

The output of this unit is not a direct inventory update. It is a set of candidate fields plus extraction provenance that the parent enrichment reconciler can evaluate.

## Retrieval Boundaries

Automatic retrieval is allowed only for:

- product page URLs returned by LCSC or DigiKey APIs
- PDF URLs returned directly by those APIs or discovered from those API-derived pages

Retrieval should capture:

- requested URL
- final resolved URL
- content type
- HTTP status
- timing
- redirect chain when relevant
- retrieval failure classification

If retrieval leaves the trusted-source boundary, the attempt should fail closed unless the broader search flow has already been user-confirmed.

## Content Classification

Fetched artifacts should be classified into at least:

- structured HTML product page
- PDF document
- unsupported content
- retrieval failure

Classification should be based on response headers plus lightweight content inspection, not URL suffix alone.

## Extractor Selection

Extractor selection should be layered.

First layer: source-specific deterministic extractors for known providers such as DigiKey and LCSC.

Second layer: generic structured-data extraction for HTML sources, using machine-readable metadata such as JSON-LD, table structures, and stable labeled sections.

Third layer: generic PDF text extraction for source-backed datasheets.

The system should prefer a narrower deterministic extractor over a broader heuristic one whenever both are available.

## HTML Extraction Strategy

HTML extraction should prefer this order:

1. embedded structured data
2. stable labeled product detail sections
3. provider-specific selectors/parsers
4. bounded text-pattern fallback

The extractor should not attempt open-ended semantic scraping of the whole page. It should target the inventory field set explicitly.

For each extracted value, the extractor should emit both the candidate value and the local page evidence used to derive it.

## PDF Extraction Strategy

PDF extraction should treat the document as a fallback metadata source, not a full technical document parser.

The first pass should attempt deterministic extraction of the target field set from the first pages and obvious part-summary sections before considering broader text scanning.

PDF extraction should emit:

- candidate fields
- page references
- extraction snippets or anchors
- extraction confidence markers

If the PDF contains multiple ordering variants or family-level part listings, extraction must not guess the exact variant without explicit supporting evidence.

## Output Contract

The extraction subsystem should return a normalized result shape containing:

- source locator
- source kind
- extractor used
- extracted candidate fields
- evidence handles for each candidate
- extraction warnings
- extraction status

A successful extraction may still be incomplete. Extraction completeness and update eligibility are separate decisions.

## Failure Semantics

This unit should distinguish at least:

- retrieval timeout
- retrieval denied or blocked
- unsupported content type
- extractor not available
- extractor produced no candidates
- extractor produced ambiguous candidates
- extractor produced partial candidates

These states should flow upward intact rather than being collapsed into a single generic lookup failure.

## Tradeoffs

Site-specific extractors increase maintenance cost, but they provide much more reliable metadata recovery than a purely generic scraper.

Generic extraction broadens coverage, but it must remain bounded so that extraction does not silently become freeform web scraping.

PDF extraction can recover fields that are absent from product pages, but it introduces more ambiguity and weaker structure than HTML extraction.

## Open Questions

- Should known providers get dedicated extractor modules immediately, or should implementation start with one generic HTML extractor plus one DigiKey-specific parser?
- What raw evidence should be stored durably versus referenced indirectly in provenance records?

## Readiness

This unit is at medium readiness. The retrieval boundary, staged extractor architecture, and failure model are concrete, but extractor specialization strategy and evidence retention policy still need to be made explicit.
