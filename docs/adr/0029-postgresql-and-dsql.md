# 0029: Use existing PostgreSQL and share SQL with the DSQL experiment

Status: Accepted for PostgreSQL; DSQL experiment superseded by [0033](0033-serverless-compatible-self-hosting.md)

Recorded: 2026-10-13

## Context

The operator already runs PostgreSQL. Maintaining SQLite plus another runtime
storage engine adds schema and behavior drift. The core requirement is finding
parts with exact identities, unit-aware nominal values and evidenced electrical
qualifiers; a separate search service is unnecessary without measured need.

## Decision

Use a dedicated PostgreSQL database/login for local and current production use.
Retire SQLite runtime adapters after an explicit verified migration; retain the
original files as recovery sources. Inventory, approvals, receipts, executions and
conversations share a database. Keep SQL candidate filtering and typed domain
search/ranking, including specification qualifiers. Keep evidence authoritative.

Use the same SQL adapter with an IAM-authenticated, verified-TLS DSQL connector for
the AWS experiment. The operator provisions the cluster. PostgreSQL compatibility
is not equivalence: validate supported DDL, optimistic concurrency, authentication,
transaction bounds and recovery against the live service. Avoid savepoints,
implicit schema changes and database-specific domain rules. This supersedes the
persistence deferral in [0008](0008-deferred-aws-direction.md) and SQLite composition
in [0001](0001-domain-and-storage-boundaries.md); hosting boundaries remain.

## Consequences

Existing PostgreSQL adds no new database instance to the current infrastructure.
The app depends on that server and operator-managed credentials/backups. PostgreSQL
makes development representative of the shared SQL; tests use disposable schemas.
Write units use repeatable-read snapshots and a common revision-row dependency to
prevent approval write skew. Measure contention before replacing this simple guard.
The initial SQLite importer is PostgreSQL-specific; DSQL's transaction limits need
a separate copy procedure. No live DSQL suitability or cost claim follows from
local tests. DSQL has usage-based charges; compare total measured cloud costs before
moving production, while Terraform/auth/orchestration remain separate decisions.

Sources: [DSQL SQL compatibility](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-supported-sql-features.html),
[concurrency](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-concurrency-control.html),
[IAM tokens](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/SECTION_authentication-token.html),
[pricing](https://aws.amazon.com/rds/aurora/dsql/pricing/).
