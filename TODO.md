# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Decisions and rationale belong in [ADRs](docs/adr/README.md).

Cloud experiments remain deferred while local ingestion and evidence-backed
electrical lookup are improved.

## Next: electrical requirements and enrichment

- Extend specification and condition definitions from representative source-backed
  requests, including operating ranges, derating, and additional categories.
- Define identity for connectors, modules, kits, and unmarked stock from concrete
  ingestion failures. Consider typed category attributes and explicit variant
  identity without inventing ordering codes or merging uncertain stock. Rehearse
  any migration on copies while preserving IDs, quantities, and provenance.
- Measure candidate-read costs and result sizes before adding indexed
  specification queries; use the existing local repository first.

## Source discovery and extraction evaluation

- Recheck the supplied-source [acceptance cases](evaluation/enrichment/acceptance.json),
  clarification and photo identification when changing prompts or models, using
  small, explicitly bounded live experiments. Preserve unknown fields until supported.
- Expand the [electrical source cases](evaluation/enrichment/electrical_sources.json)
  with new variants, operating ranges and ambiguous identities.
- Preserve applicable headings, vertically merged table cells, and distant
  measurement-method sections in bounded source selection. Exercise different
  supplier layouts; do not solve missing context with part-specific rules.
- Extend conservative condition comparison from independently reviewed source
  cases when additional units or representations are needed. Evaluate qualifier
  completeness separately from extraction coverage and cross-run matching.
- Measure repeatability of electrical table interpretation across models and
  sources. Check global test conditions, min/max endpoints, paired load ratings
  and reference-circuit values; authentic citations can still support an incorrect
  proposal. Use independent source review, latency and measured usage when
  selecting a model; structural checks cannot establish factual quality.
- Measure automatic source discovery separately from supplied-source extraction
  before choosing integrations. Remove source paths that do not justify their
  maintenance burden; distinguish retrieval failure from no matching part.
- Keep local OCR photos in memory instead of writing temporary image files,
  following the [storage boundaries](docs/adr/0005-storage-and-retention.md).

## Durable ingestion experiment

Before starting this experiment, resolve the cloud constraints listed below.

- Define the acceptance scenario: identify a photo or request clarification,
  retrieve evidence for the exact part, stage a review, restart while awaiting
  approval, then apply the approved operation once despite duplicate delivery.
  Persist derived candidates and progress rather than image bytes. If an
  interrupted photo stage cannot resume, request a fresh image explicitly.
- Compare Lambda durable functions with Step Functions Standard using the same
  scenario; verify Python/async and Terraform integration and measure paid-stage
  retries, latency, and cost.
- Demonstrate authenticated progress delivery, browser reconnect, explicit
  cancellation, concurrent requests, worker replacement, and retry behavior.
  Keep model/tool context distinct from user-visible conversation events.
- Record orchestration, persistence, and ingress tradeoffs in numbered ADRs,
  superseding the [deferred cloud direction](docs/adr/0008-deferred-aws-direction.md)
  when decisions change. Use run artifacts for correctness, latency, and cost
  evidence; do not select services by implication.

## Cloud work

- Resolve budget, intended access, region, acceptable downtime, maximum data loss,
  and recovery time before dependent infrastructure choices.
- Use repository-injected scenarios plus adapter-specific atomicity, concurrency,
  and recovery checks to evaluate persistence candidates. Rehearse migration on
  a copy, preserving identities, evidence, ordering, and retry protection.
- Use the durable ingestion experiment to select persistence, orchestration, and
  ingress. Compare idle and realistic usage costs, including paid AI/retrieval
  stages and retries; justify each service against the smallest suitable stack.
- Implement selected cloud storage adapters against the inventory/approval
  unit-of-work and conversation repository contracts. Exercise the same domain,
  atomicity, retry, and recovery scenarios on local and cloud storage; migrate a
  copy and verify inventory, evidence, and history.
- Build reproducible AWS serverless infrastructure with Terraform, preserving
  local operation. Provide HTTPS, authentication, secure configuration, release
  automation, monitoring, and tested backup/restore.
- Demonstrate fresh-account deployment, upgrade/rollback, recovery, performance,
  and teardown. Record reusable procedures, not permanent pass/fail claims.

See [cloud hosting decisions](docs/adr/0008-deferred-aws-direction.md). These tasks do not
select a database, model, or AWS service by implication.
