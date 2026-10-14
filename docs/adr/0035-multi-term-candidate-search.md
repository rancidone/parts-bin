# 0035: Narrow candidate searches with terms across committed fields

Status: Accepted

## Context

Candidate search accepts one contiguous fragment in a part number, manufacturer
or description. Combining a manufacturer and package cannot narrow discovery when
those words are stored in different fields of the same inventory record.

## Decision

Interpret a candidate query as whitespace-separated literal terms. Require every
term to match the same committed record, allowing terms to match different fields:
part number, manufacturer, description, category, value or package. Fold case for
text discovery, preserve punctuation, and keep the existing query and page bounds.
Do not introduce wildcard, quotation, fuzzy-match or natural-language operators.

Keep discovery in the typed domain service and expose it through the existing
narrow agent tool. Preserve exact filters, per-record stock thresholds, stable
pagination and candidate labels. Pending reviews and specifications remain outside
text discovery. Use structured value/specification searches for unit equivalence
and source-backed electrical suitability. Candidate matching cannot merge ordering
suffixes, prove identity, allocate stock or mutate inventory.

## Consequences

Queries such as `Nexperia 5350 SOT23` can narrow one stored part across several
fields without supplier retrieval or a model call during tool execution. Multiword
text now matches terms in any order rather than requiring a contiguous phrase.
Chat still needs its normal model request to select the tool. Unknown markings,
unrecorded metadata and alternate unit notation remain outside literal matching.
No schema, search service or deployment configuration changes are needed.
