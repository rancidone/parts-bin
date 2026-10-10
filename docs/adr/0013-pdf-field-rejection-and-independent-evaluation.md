# 0013: Isolate invalid PDF values and evaluate source completeness independently

Status: Accepted

## Context

One malformed electrical value can discard an otherwise useful source proposal.
A coverage score checks field presence and minimum conditions, while source
completeness requires the governing headings, footnotes and exact value/test
pairs. OCR recognition confidence measures another property entirely.
This refines [0011](0011-best-effort-pdf-extraction.md).

## Decision

Validate electrical facts individually. Omit invalid values or condition values
and report the field and validation reason in the review assessment. Identity,
quotation authenticity, schema integrity, duplicate names, unsupported fields
and rating-basis substitutions still reject the proposal. Retained facts remain
subject to existing approval and matching rules.

Label the existing numeric assessment as extraction coverage in the UI. Do not
present it as a calibrated probability of correctness. Keep its explanation and
page links alongside missing, incomplete and rejected fields.

Evaluate completeness against expectations reviewed from rendered manufacturer
pages and pinned to their source hashes. Never supply those expectations to the
model. Compare value/basis, governing qualifiers and value/condition pairs;
source drift invalidates numeric scores. Genuine quotation-to-claim association
and equivalent unlisted wording still require human review.

Keep raster OCR as a bounded, explicitly selected-page evaluation tool. Measure
recognition separately from electrical completeness. The pilot demonstrated
recovery of text but also incorrect current/test-condition pairing and loss of
merged-table facts. It does not establish scan discovery, naturally scanned
source performance or sufficient quality for automatic production OCR.

## Consequences

Useful facts survive isolated value failures with visible omissions. This does
not repair a malformed value, infer omitted qualifiers or weaken evidence checks.
Operators can compare native and raster extraction without treating either
coverage or OCR confidence as factual certification. Production OCR requires a
representative scan corpus, reliable page selection and tested review semantics.
