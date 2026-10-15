# Architecture decision records

These records capture existing project choices and their rationale, not feature
status. Recording them does not select additional services or change behavior.

- [0001: Keep domain rules independent of hosting and storage](0001-domain-and-storage-boundaries.md)
- [0002: Use one OpenAI Responses interface](0002-openai-responses-interface.md)
- [0003: Commit mutation outcomes with effects and recover explicitly](0003-durable-effects-and-explicit-recovery.md)
- [0004: Enrich on demand from evidence, through review](0004-source-backed-enrichment.md)
- [0005: Keep photos ephemeral and caches separate from authoritative data](0005-storage-and-retention.md)
- [0006: Normalize in the domain without rewriting or merging historical stock](0006-normalization-without-stock-merges.md)
- [0007: Match electrical requirements against reviewed, qualified facts](0007-reviewed-electrical-facts.md)
- [0008: Target AWS and Terraform without selecting a cloud stack yet](0008-deferred-aws-direction.md) (superseded by 0033)

- [0009: Bound model context independently of retained data](0009-bounded-model-context.md)
- [0010: Cite supplied electrical evidence by server-owned passages](0010-supplied-electrical-passages.md)

- [0011: Extract PDFs best effort with explicit coverage and visual review](0011-best-effort-pdf-extraction.md)
- [0012: Preserve semiconductor subtype and measurement conditions](0012-qualified-semiconductor-facts.md)
- [0013: Isolate invalid PDF values and evaluate source completeness independently](0013-pdf-field-rejection-and-independent-evaluation.md)
- [0014: Accept explicit device identity alongside shipping codes](0014-device-identity-in-supplied-datasheets.md)
- [0015: Treat a saved datasheet as the operator's part association](0015-saved-datasheet-association.md)
- [0016: Reserve governing specification context before repeated ordering rows](0016-governing-specification-context.md)

Use one numbered record per decision with **Status**, **Context**, **Decision**,
and **Consequences**. Do not append implementation progress, test counts, or
current configuration. Correct factual mistakes when necessary; when a decision
changes, add a new ADR and mark the old one superseded with a link.

Keep unresolved proposals in [TODO](../../TODO.md), commands in
[operations](../operations.md), and configuration in
[the sample config](../../config.example.toml).

- [0017: Check a bounded BOM in one domain operation](0017-whole-bom-availability-check.md)
- [0018: Require field decisions and source-bound identity citations](0018-explicit-extraction-decisions-and-identity-passages.md)
- [0019: Batch accepted specification reads for candidate searches](0019-batched-accepted-specification-reads.md)

- [0020: Keep unnumbered non-passive stock as distinct physical groups](0020-distinct-unnumbered-stock.md)

- [0021: Compare explicit condition intervals with shared unit-aware parsing](0021-unit-aware-condition-intervals.md)

- [0022: Compare whole specification value intervals](0022-specification-value-intervals.md)

- [0023: Cover requested conditions within documented operating intervals](0023-operating-condition-coverage.md)

- [0024: Return explained partial inventory matches](0024-explained-partial-inventory-matches.md)
- [0025: Drain requests during deployment and preserve recoverable status](0025-graceful-deployment-and-recovery-status.md)
- [0026: Advance agent executions at durable stage boundaries](0026-bounded-agent-worker-steps.md)
- [0027: Persist intake and separate model submission from collection](0027-durable-intake-and-background-responses.md)

- [0028: Commit mutations and bulk outcomes per part](0028-per-part-mutation-transactions.md)
- [0029: Use existing PostgreSQL and share SQL with the DSQL experiment](0029-postgresql-and-dsql.md)

- [0030: Dispatch durable stages through PostgreSQL and signed OpenAI notifications](0030-durable-dispatch-and-openai-webhooks.md)

- [0031: Reconnect chat progress through read-only event cursors](0031-read-only-chat-progress-reconnect.md)

- [0032: Cancel executions at durable stage boundaries](0032-stage-boundary-cancellation.md)

- [0033: Keep serverless-compatible execution on existing self-hosted infrastructure](0033-serverless-compatible-self-hosting.md)

- [0034: Keep PostgreSQL as the only persistence adapter](0034-postgresql-only-persistence.md)

- [0035: Narrow candidate searches with terms across committed fields](0035-multi-term-candidate-search.md)

- [0036: Browse candidate inventory through the shared domain search](0036-direct-inventory-candidate-search.md)

- [0037: Restore Inventory search criteria from bookmarkable URLs](0037-bookmarkable-inventory-search.md)

- [0038: Filter Inventory by nominal value and exact package](0038-direct-nominal-and-package-filters.md)

- [0039: Suggest exact package spelling from committed inventory](0039-inventory-package-suggestions.md)

- [0040: Suggest stored nominal values without changing unit matching](0040-inventory-nominal-value-suggestions.md)

- [0041: Cross-filter Inventory suggestions through shared domain search](0041-cross-filtered-inventory-suggestions.md)

- [0042: Apply candidate text search to Inventory suggestions](0042-text-aware-inventory-suggestions.md)

- [0043: Derive Inventory category options from matching inventory](0043-filtered-inventory-category-options.md)

- [0044: Expose stored inventory through read-only MCP](0044-read-only-inventory-mcp.md)

- [0048: Deduplicate bulk creation in the domain transaction](0048-domain-deduplication-for-bulk-creation.md)
