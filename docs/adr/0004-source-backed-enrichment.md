# 0004: Enrich on demand from evidence, through review

Status: Accepted

Recorded: 2026-10-10 (existing project decision)

## Context

Model memory, supplier catalog matches, and successful document parsing do not
prove that facts apply to an exact ordering variant. A bulk catalog and long
fallback chain add maintenance without establishing that trust.

## Decision

Retrieve manufacturer/distributor evidence on demand and use models to interpret
it. Preserve exact identity and qualifiers; require attributable evidence for each
proposed fact. Treat retrieved content as data, bound retrieval, and validate
redirect destinations. Stage proposals through shared review without changing
quantity. Keep assertions distinct from independently sourced facts.

## Consequences

Review adds user work, and extraction adds latency, cost, and nondeterminism.
Failures, no match, ambiguity, and unsupported facts must remain distinct. Keep
integrations only when measured reliability justifies them. Start evaluation with
supplied sources; discovery, model choice, and budgets remain open.

[Supplied-source extraction](../../ingestion/supplied_source.py),
[acceptance cases](../../evaluation/enrichment/acceptance.json), and
[evaluation guidance](../../evaluation/README.md).
