# 0034: Keep PostgreSQL as the only persistence adapter

Status: Accepted

## Context

ADR 0033 cancelled the cloud deployment roadmap but retained an unused DSQL
connector. That connector and its AWS SDK dependencies add maintenance and package
weight without serving the supported Docker/PostgreSQL installation. The owner
has requested their removal.

## Decision

Use PostgreSQL as the only runtime persistence adapter. Remove the DSQL connector,
its setup command, AWS dependency declarations and their unused transitive packages.
Run the former shared adapter scenarios directly against isolated PostgreSQL schemas,
retaining search, mutation receipts, approvals, concurrency, leases and recovery
checks. Remove the live DSQL fixture and configuration environment variables.

This supersedes ADR 0029's persistence/adapter choice and ADR 0033's connector
retention. Retain PostgreSQL's existing database, explicit schema installation,
verified SQLite migration procedure and protected recovery sources.

Preserve the serverless-compatible worker architecture from ADR 0033: durable
intake, bounded stages, separate model submission/collection, external checkpoints,
idempotent effects, event cursor recovery and stage-boundary cancellation. These
application properties do not require an AWS SDK or a cloud-specific database.

## Consequences

Deploy both HTTP and worker images using the existing Docker procedure. No database
migration or configuration change is needed. Inventory, evidence, conversations,
approvals and execution checkpoints retain their existing formats and identities.
PostgreSQL tests exercise the supported storage engine without provider-specific
fixture branches. Historical ADRs retain the rationale for the retired experiment;
new cloud support would require a separate explicit decision.
