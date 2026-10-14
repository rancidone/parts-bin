# 0033: Keep serverless-compatible execution on existing self-hosted infrastructure

Status: Accepted; DSQL connector retention superseded by [0034](0034-postgresql-only-persistence.md)

## Context

The application needs to find the owner's parts and operate reliably on existing
infrastructure. Moving to AWS adds provisioning, service selection, migration and
operational work that the owner no longer wants to pursue. The execution changes
made for replaceable workers are useful on the current host as well.

## Decision

Continue self-hosting with Docker and PostgreSQL. Cancel the AWS/cloud deployment
roadmap, including Terraform provisioning, managed orchestration comparisons,
AWS ingress experiments and DSQL provisioning/migration. This supersedes the
hosting direction in ADR 0008 and the DSQL experiment in ADR 0029; PostgreSQL
persistence and the verified SQLite retirement remain in force.

Keep execution compatible with short-lived, replaceable workers. Preserve durable
intake, separate model submission and result collection, bounded stages, leases,
checkpointed mutation receipts, safe retries, cursor recovery and explicit
stage-boundary cancellation. Keep authoritative data outside worker processes and
hosting concerns outside typed domain rules. These remain application architecture
constraints, independent of any cloud provider or deployment commitment.

Prioritize inventory workflows and recovery on the current installation. Introduce
infrastructure only for a concrete operational need. Polling remains the default;
OpenAI webhook activation remains an optional operator action. Future cloud work
requires a new explicit decision, rather than automatically resuming a deferred
migration plan.

## Consequences

No infrastructure migration or data movement is needed. The existing PostgreSQL
server, HTTP service and polling worker retain their operating and recovery
procedures. Network-accessible hosting still needs HTTPS, authentication, protected
configuration, monitoring and tested backups.

The optional DSQL connector remains a historical experiment outside supported
hosting scope; it does not impose a database choice or justify further AWS work.
Historical ADRs retain their context, including prior AWS references, but this
record governs the current direction. Serverless compatibility is a design goal,
not a claim of tested support for an unspecified platform or invocation limit.
