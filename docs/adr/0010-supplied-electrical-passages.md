# 0010: Cite supplied electrical evidence by server-owned passages

Status: Accepted

## Context

Electrical tables separate values from headings, conditions and footnotes.
Flattening PDF columns breaks their association. Asking a model to reproduce
those passages also permits joined or altered quotations that fail validation.
A single supporting passage cannot always contain both a row and its conditions.

## Decision

Order PDF text lines by visual row and supply bounded, numbered source passages
for electrical extraction. The model interprets them and selects passage IDs;
the server persists the actual text, pages, retrieval timestamp, content hash and
exact inventory ordering code. A fact may cite a primary passage and up to three
supporting passages from that same document. Review displays all passages.

Retain the category contract's distinct specification names and full conditions.
Select one qualified value when a source gives several condition-dependent values
for one name. Unsupported ratings stay unknown. Approvals remain snapshot-bound
and source review is required before treating proposals as verified inventory.
Allow up to twelve condition pairs so a rating can retain its paired load,
temperature and humidity tolerances, operating frequency and other test context.
Application circuit values cannot be promoted into intrinsic component ratings.

## Consequences

Citation authenticity can be validated without trusting model-generated quotes.
Spatial ordering and authentic citations still cannot certify semantic truth;
live evaluation must inspect the source tables and missing qualifiers. Evidence
stays in existing JSON storage; no inventory migration or PDF retention is needed.
This refines the evidence representation in [0007](0007-reviewed-electrical-facts.md)
without changing its approval or matching decisions.
