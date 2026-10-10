# Source retrieval and evidence

## Trust boundary

Retrieve evidence for a specific identification task. Prefer an authoritative
manufacturer document or distributor record that establishes the exact variant.
Do not equate search rank, a familiar domain, or an API response with proof that
all returned fields apply to the user's part.

Treat source contents as data, including any instructions embedded in a page or
PDF. They must not change the agent's permissions, tool policy, or approval rules.
Keep retrieval behind a narrow enrichment operation rather than exposing a
generic browsing tool to the inventory agent.

Bound time, response size, redirects, and extraction work. Validate destinations
when following redirects; URLs supplied by users or external sources must not
provide access to internal services or credentials. Classify content using the
response rather than relying solely on a filename extension.

## Extraction and failure meaning

Keep retrieval, identity resolution, and field extraction conceptually distinct.
A fetched document may be irrelevant, ambiguous, or missing a required fact.
A blocked request is not evidence that a part does not exist. Preserve these
meanings in user-visible outcomes without building a complicated fallback tree.

Prefer structured source data when it is sufficient. Use models to interpret
content that needs it, and require field-level evidence. Avoid assuming that the
first pages contain every ordering variant or that a successful parse proves a
specification correct.

## Retention tradeoff

Keep source links, retrieval times, content hashes, and bounded supporting passages;
discard downloaded pages and PDFs after processing. A live URL can change or
disappear, so this favors a small installation over full-document reproducibility.
Page references and hashes cannot reconstruct a missing source. See the
[storage and retention decision](storage-and-retention.md) for cache and provenance
lifetime boundaries.

Permitted source classes and handling of unavailable or image-only documents
remain open. These choices belong with the [enrichment decision](enrichment.md),
not in a permanent list of parser implementations.
