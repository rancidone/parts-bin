# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Decisions and rationale belong in [ADRs](docs/adr/README.md).

Cloud experiments remain deferred while local ingestion and evidence-backed
electrical lookup are improved.

## Next: electrical requirements and enrichment

- Improve bounded context selection for the op-amp
  [source cases](evaluation/enrichment/electrical_sources.json): preserve stress,
  operating-supply and frequency-response sections alongside exact ordering rows.
  Include governing merged-cell geometry for quiescent-current temperature.
  Reevaluate B/BA variants against the independent source expectations with
  bounded live runs after these omissions are corrected.
- Define further operating ranges and derating from concrete source-backed
  requests; extend fields and condition comparison only where those cases need it.
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
- Extend the independently reviewed
  [source expectations](evaluation/enrichment/electrical_expectations.json) to
  resistors and switches. Add source cases for gaps exposed by evaluation,
  rather than duplicating existing variant and ambiguity cases.
- Evaluate retention of governing headings, vertically merged cells, and distant
  measurement-method sections in bounded source selection across supplier layouts.
  Fix measured omissions without part-specific rules.
- Measure repeatability of electrical table interpretation across models and
  sources. Check global test conditions, min/max endpoints, paired load ratings
  and reference-circuit values against independent source expectations. Separate
  qualifier completeness from extraction coverage and cross-run matching; use
  source review, latency and measured usage when selecting a model.
- Evaluate raster OCR on representative scanned datasheets, including page
  selection and table association, before deciding whether to add production OCR.
  Keep recognition measurements separate from electrical correctness.
- Measure automatic source discovery separately from supplied-source extraction
  before choosing integrations. Remove source paths that do not justify their
  maintenance burden; distinguish retrieval failure from no matching part.

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
