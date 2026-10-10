# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Design rationale belongs in [docs/design/](docs/design/).

## Next: durable ingestion experiment

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

## Enrichment simplification

- Define category-specific specification shapes and representative searches for
  resistors, transistors, and audio ICs. Keep units, test conditions, source
  evidence, and review semantics explicit; compare document storage with JSON
  in relational storage before choosing a backend. See
  [flexible part modeling](docs/design/flexible-part-model-and-identity.md).
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

## Cloud work

- Resolve budget, intended access, region, acceptable downtime, maximum data loss,
  and recovery time before dependent infrastructure choices.
- Use the durable ingestion experiment to select cloud persistence, orchestration,
  and ingress before expanding the deployment.
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
