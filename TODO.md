# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Design rationale belongs in [docs/design/](docs/design/).

## Next: persistence decoupling and serverless

- Separate application composition from module-level HTTP startup. Inject the
  installation's repositories and runtime dependencies so HTTP and future workers
  can share the same domain without assuming local database files.
- Isolate the supplied-source enrichment cache behind a small storage contract.
  Keep its expiry and recovery semantics separate from authoritative inventory,
  provenance, conversation history, and operation outcomes.
- Use repository-injected scenarios plus adapter-specific atomicity, concurrency,
  and recovery checks to evaluate a cloud persistence candidate. Rehearse migration
  on a copy, preserving identities, evidence, event ordering, and retry protection.
- Resolve the AWS experiment's budget and recovery constraints before selecting
  cloud services; justify each service against the smallest viable stack.

## Resistor requirements search

- Deliver one local workflow: accept evidenced resistor specifications through
  review, then answer "Find four 10 kΩ resistors with tolerance of 1% or better,
  rated for at least 0.25 W under the stated conditions." Use the existing SQLite
  repository and agent runtime; add no external search service.
- Define typed resistance, tolerance, and rated-power facts with normalized units,
  applicable conditions, and field provenance. Preserve existing inventory and
  leave missing historical specifications unknown. Prevent uncertain or differing
  specification variants from being silently merged into the same stock.
- Extend staging and approval to these facts, preserving exact identity and
  quantity. Start with supplied-source evidence to isolate extraction and review
  from supplier discovery; do not infer ratings from descriptions or model memory.
- Extend the narrow search contract with validated resistor requirements and
  minimum stock. Return bounded matches with supporting facts and evidence;
  identify incomplete candidates separately. Keep pending proposals out of
  confirmed matches.
- Exercise unit equivalence, inclusive comparison boundaries, combined constraints,
  unknown facts, insufficient stock, variant identity, and search before/after
  approval. Test natural-language interpretation separately from deterministic
  matching; evaluate live extraction quality separately from recorded fixtures.

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

## Enrichment simplification

- Define searchable category-specific specifications for resistors, transistors,
  and audio ICs. Let agents discover supported fields and translate natural-language
  requirements into validated numeric, range, categorical, and stock constraints.
  Exercise unit conversion, limit qualifiers, missing evidence, combined filters,
  boundary values, and match explanations. Keep pending proposals separate from
  committed matches; compare indexed document storage with relational queries
  over typed facts and JSON before choosing a backend. See
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
