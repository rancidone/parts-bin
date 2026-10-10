# Project documentation

Documentation explains decisions, intent, tradeoffs, and how to operate or
understand Parts Bin. Code defines implemented behavior; executable checks and
their run output provide evidence. A decision document is not a feature matrix,
readiness certificate, or test report.

## Reading guide

- [Product boundaries](design/product-contract.md): what the app is for and what
  users should be able to trust.
- [Architecture](design/architecture.md): ownership boundaries and their rationale.
- [Enrichment](design/enrichment.md): source-backed proposals and the decision to
  stop maintaining a bulk supplier catalog.
- [Source retrieval](design/source-retrieval-and-extraction.md): trust boundaries
  and evidence limitations.
- [Storage and retention](design/storage-and-retention.md): ephemeral photos,
  compact provenance, reusable enrichment, and proposed expiry defaults.
- [Configuration](design/configuration.md): explicit runtimes and secret ownership.
- [Cloud hosting](design/cloud-hosting.md): AWS/serverless direction and open choices.
- [Part identity](design/flexible-part-model-and-identity.md): a design question,
  not an approved schema migration.
- [Evaluation](design/evaluation.md): what different kinds of checks can establish.
- [Operations](operations.md): local diagnostics and recovery guidance.

Use [the root README](../README.md) for setup and [TODO](../TODO.md) for upcoming
work. For implementation details, follow the code links in the relevant guide.

## Writing conventions

Record a decision with its reason, consequences, and unresolved questions.
Distinguish an agreed direction from an option still being considered. Update a
decision when its rationale changes, not whenever a function or test is added.

Keep configuration examples in the sample config, schemas in code, and test
assertions in tests. Do not copy endpoint/tool lists, table definitions, coverage
matrices, passing counts, or implementation-completion claims into prose.
Practical commands and conceptual examples are useful when they help a reader
perform a task; they are not proof that the task has succeeded.

Consolidate overlapping explanations. Git history preserves superseded drafts
and migration instructions; an in-tree archive of contradictory specifications
is not required.
