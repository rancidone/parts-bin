# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Decisions and rationale belong in [ADRs](docs/adr/README.md).

Prepare the application for serverless workers while preserving local operation.
AWS service selection and paid cloud experiments still require deployment
constraints and measured tradeoffs.

## Serverless preparation

- Separate durable request intake from worker invocation. Persist a stable
  request/execution identity before dispatch, reject conflicting duplicate payloads,
  and recover a lost acknowledgement without creating a second execution. Keep
  photo bytes ephemeral and request resubmission when initial analysis cannot resume.
- Define dispatch and continuation around the bounded worker entry point,
  including duplicate delivery, lost scheduling acknowledgement, approval waits,
  cancellation and interrupted paid stages. Avoid relying on in-process tasks
  after an HTTP response; test worker replacement with injected repositories.
- Separate progress delivery from execution. Reconnect using stored event cursors,
  preserving ordering and terminal/approval state without restarting work. Validate
  the chosen authenticated delivery adapter during the cloud experiment.

## Deferred enrichment measurements

Prioritize product features. Resume paid evaluation only for a concrete user
request or a production failure that justifies its cost; accept incomplete
extraction and keep unsupported fields unknown.

- Extend independently reviewed source cases from actual stock and ingestion
  failures, including marked LED intensity grades, regulator voltage variants,
  incomplete transformer packaging codes, and ambiguous stock.
- Review production overlapping-stock, partial-match and specification-search
  conversations when those cases accumulate; distinguish unresolved row identity
  from missing stock, verify explicit partial-match explanations and inspect
  condition qualifiers in the resulting answers.
- When evaluation is justified, distinguish retrieval and source-selection
  failures, missing facts, server rejections, and qualifier mismatches. Compare
  model interpretation against independent source expectations rather than
  treating schema success as correctness.
- Evaluate scanned-datasheet OCR and automatic source discovery separately when
  concrete requests need them; retain integrations only when reliability justifies
  their complexity and cost.

## Deferred local requirements

- Add derating or coverage beyond explicitly documented operating conditions
  only from concrete source-backed requests. Containment of documented operating
  ranges does not establish behavior outside them or turn test points into guarantees.
- Add typed category attributes for connectors, modules and kits only when
  concrete ingestion failures require structured variant descriptions. Preserve
  separate unnumbered physical groups and rehearse any migration on copies.

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
- Compare total idle and realistic usage costs for the candidate stacks from the
  durable ingestion experiment, including paid AI/retrieval stages and retries;
  justify each service against the smallest suitable stack.
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
