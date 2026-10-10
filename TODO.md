# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Design rationale belongs in [docs/design/](docs/design/).

## Enrichment simplification

- Exercise source-backed extraction against the representative
  [acceptance cases](evaluation/enrichment/acceptance.json). Start with a supplied
  datasheet to separate extraction quality from source discovery.
- Select a bounded retrieval/model approach using measured correctness, useful
  clarification, latency, and cost. Remove source paths that do not justify their
  maintenance burden.
- Connect proposals to domain validation and review; check failed retrieval,
  conflicting identity, and data preservation through executable tests.

## Cloud work

- Resolve budget, intended access, region, acceptable downtime, maximum data loss,
  and recovery time before dependent infrastructure choices.
- Evaluate durable cloud persistence, approval state, request concurrency, and
  idempotent retries. Demonstrate authenticated streamed agent interaction before
  committing to a service topology.
- Build reproducible AWS infrastructure with Terraform, preserving local operation.
- Address secret delivery, release automation, monitoring, backup and restoration.
- Demonstrate fresh-account deployment, upgrade/rollback, recovery, performance,
  and teardown. Record reusable procedures, not permanent pass/fail claims.

See [cloud hosting decisions](docs/design/cloud-hosting.md). These tasks do not
select a database, model, or AWS service by implication.
