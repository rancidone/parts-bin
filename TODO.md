# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Design rationale belongs in [docs/design/](docs/design/).

## Next: local chat and lookup

- Add conversation selection and browser-level regression tests around reconnect,
  photo resubmission, approval rendering, and interactions across multiple tabs.
  Preserve historical provider identity and keep recovery explicit.
- Evaluate natural-language inventory lookup through the existing narrow tool
  contract. Exercise value notation, exact ordering suffixes, package ambiguity,
  stock availability, and useful clarification. Keep committed inventory separate
  from pending enrichment and measure live model interpretation independently
  of deterministic matching.

Cloud experiments are deferred while these local workflows and evidence-backed
lookup are improved.

## Electrical requirements and enrichment

- Extend supplied-source extraction to the electrical fact/review contract. Start
  with the resistor query: four 10 kΩ parts, tolerance at most 1%, and rated power
  at least 0.25 W under the stated conditions. Evaluate representative capacitor,
  BJT, MOSFET, inductor, transformer, and switch sources independently; these are
  initial categories, not an exhaustive taxonomy.
- Measure live extraction and natural-language interpretation separately from
  deterministic matching. Verify exact variants, qualifiers, missing facts, useful
  clarification, and source passages before accepting results.
- Extend specification and condition definitions from representative requests,
  including operating ranges, derating, additional component categories, and
  explicit variant identity for stock without an ordering code. Preserve evidence
  and distinguish assertions, pending proposals, and accepted source facts.
- Measure candidate-read costs and result sizes before adding indexed specification
  queries or pagination; use the existing local repository first.

## Enrichment simplification

- Exercise source-backed extraction against the representative
  [acceptance cases](evaluation/enrichment/acceptance.json). Start with a supplied
  datasheet to separate extraction quality from source discovery.
- Select a bounded retrieval/model approach using measured correctness, useful
  clarification, latency, and cost. Remove source paths that do not justify their
  maintenance burden.
- Connect proposals to domain validation and review; check failed retrieval,
  conflicting identity, and data preservation through executable tests.
- Add reusable enrichment results and in-flight deduplication with bounded paid
  stages. Keep accepted provenance independent of cache expiry; exercise refresh,
  transient failure, and interrupted-job retry behavior.
- Apply the [storage boundaries](docs/design/storage-and-retention.md): keep photos
  out of durable history/queues and discard retrieved documents after extraction.

## Durable ingestion experiment

- Define the acceptance scenario: identify a photo or request clarification,
  retrieve evidence for the exact part, stage a review, restart while awaiting
  approval, then apply the approved operation once despite duplicate delivery.
  Honor the existing ephemeral-photo policy; persist derived candidates and
  progress rather than image bytes. If an interrupted photo stage cannot resume,
  request a fresh image explicitly.
- Resolve the budget, region, access, and recovery constraints needed for a small
  AWS experiment. Compare Lambda durable functions with Step Functions Standard
  using the same scenario; verify Python/async and Terraform integration.
- Demonstrate authenticated progress delivery, browser reconnect, explicit
  cancellation, concurrent requests, worker replacement, and retry behavior.
  Keep model/tool context distinct from user-visible conversation events.
- Record the orchestration, persistence, and ingress tradeoffs in
  [cloud hosting decisions](docs/design/cloud-hosting.md), using run artifacts for
  correctness, latency, and cost evidence. Do not select services by implication.

## Cloud work

- Use repository-injected scenarios plus adapter-specific atomicity, concurrency,
  and recovery checks to evaluate a persistence candidate. Rehearse migration on
  a copy, preserving identities, evidence, ordering, and retry protection.
- Resolve budget, intended access, region, acceptable downtime, maximum data loss,
  and recovery time before dependent infrastructure choices.
- Use the durable ingestion experiment to select cloud persistence, orchestration,
  and ingress before expanding the deployment. Compare idle and realistic usage
  costs, including paid AI/retrieval stages and retries; justify each added service
  against the smallest stack that meets the requirements.
- Implement the selected cloud storage adapters against the inventory/approval
  unit-of-work and conversation repository contracts. Exercise the same domain,
  atomicity, retry, and recovery scenarios on both local and cloud storage;
  migrate a copy of existing data and verify inventory, evidence, and history.
- Build reproducible AWS infrastructure with Terraform, preserving local operation.
- Address secret delivery, release automation, monitoring, backup and restoration.
- Demonstrate fresh-account deployment, upgrade/rollback, recovery, performance,
  and teardown. Record reusable procedures, not permanent pass/fail claims.

See [cloud hosting decisions](docs/design/cloud-hosting.md). These tasks do not
select a database, model, or AWS service by implication.
