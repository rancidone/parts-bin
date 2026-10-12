# 0018: Require field decisions and source-bound identity citations

Status: Accepted

## Context

Electrical extraction can omit supported rows even when selected context retains
them. Free-form metadata citations can also identify a real document page whose
relevant text was not supplied to the model, invalidating an otherwise useful
electrical proposal. Repeated ordering rows can crowd out introductory features
that establish fixed or adjustable output behavior.

## Decision

Use a required object keyed by the existing category specification names for the
electrical model response. Each entry is a qualified fact or null when evidence
is insufficient. Convert supported entries to the existing domain fact list only
after checking the complete key set. Do not add automatic paid retries or derive
missing electrical values in server code.

Electrical identity and mismatch citations select server-owned passage IDs, as
electrical facts already do under [0010](0010-supplied-electrical-passages.md).
Supplied identity fields may cite only selected passages containing their literal
identity; exact ordering-code boundaries exclude longer codes and placeholders.
If no selected passage can establish a supplied identity, the response cannot be
a proposal. Saved-link extraction still preserves the inventory label separately
from an evidenced source designation.

Retain a little more introductory context within the existing excerpt budget,
alongside the governing-section reservations in [0016](0016-governing-specification-context.md).
The server resolves passage IDs to their original text and page numbers, then
runs existing identity, evidence, qualifier and approval validation. Metadata-only
extraction keeps its short verbatim-quotation contract.

## Consequences

Every supported field receives an explicit model decision, but null can still
reflect a model omission rather than a truly absent rating. Authentic citations
do not prove table interpretation or qualifier completeness. Independent source
review and repeated live runs remain necessary. Proposals retain the existing
storage and approval contracts; no inventory or conversation migration is needed.
The policy version separates disposable cached results from the previous model
response format.
