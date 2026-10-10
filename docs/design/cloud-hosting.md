# Cloud hosting decisions

## Direction

Target AWS first, using Terraform for reproducible infrastructure that an operator
can deploy into their own account. Favor serverless execution while preserving
local-first deployment and existing data. Use the OpenAI Responses API as the single agent interface in both local and
cloud operation.

The motivation is to avoid an always-managed application VM for an intermittently
used inventory workload and to learn cloud identity, durable state, and operations.
Serverless shifts operational responsibility; it does not eliminate it or guarantee
a lower bill. Database, retrieval, model, logging, and networking costs matter too.

## Alternatives and consequences

A single VM with Docker and a persistent SQLite disk is a simpler port of a local
application, but carries host maintenance and fixed-capacity responsibilities.
The serverless direction accepts application and persistence work to reduce that
host-management burden. This is a direction to validate, not a selected Lambda
or database topology.

Durable inventory, conversations, approvals, and interrupted work must survive
execution replacement. Removing a disposable supplier catalog does not resolve
those requirements. Select cloud persistence on transaction semantics, recovery,
connection behavior, and cost. Preserve local storage support and provide an
explicit data migration path; do not make the domain depend on cloud APIs.

Validate streamed agent responses, timeouts, cancellation, retries, and concurrent
requests before committing to a compute/ingress design. Keep provider limits and
prices in the official references used at decision time rather than copied
permanently into this document.

## Operational boundaries

Keep one owner per installation. Authentication protects both UI and API;
mutation approval remains a separate application policy. Require HTTPS, secure
configuration, scoped deployment permissions, monitoring, and backup/restore.

Treat code rollback and data recovery separately: an older image may not understand
a newer schema. A release strategy must explain that boundary. Teardown must
explicitly distinguish removable infrastructure from data/backups retained for
recovery. Preserve the original local data while rehearsing migration on a copy.

## Decisions still needed

- Budget for infrastructure and external OpenAI/retrieval calls.
- Intended access, region, acceptable maintenance downtime, maximum data loss,
  and recovery time.
- Compute and ingress services, cloud database, identity provider, DNS, and
  secret delivery.
- Durable job needs, Terraform state ownership, release/rollback strategy, and
  backup retention.

Use fresh-account deployment, upgrade/rollback, restore, performance measurement,
and teardown exercises to evaluate the choices. Store results with the run or
release; this document records the reasoning, not a readiness badge.
