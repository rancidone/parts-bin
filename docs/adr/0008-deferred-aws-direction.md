# 0008: Target AWS and Terraform without selecting a cloud stack yet

Status: Accepted

Recorded: 2026-10-10 (existing project decision)

## Context

An intermittently used inventory app may benefit from less host maintenance,
but serverless introduces persistence and recovery work and does not guarantee
lower costs. Local workflows currently take priority.

## Decision

Keep AWS serverless with Terraform as the future deployment direction and defer
cloud experiments. Preserve local operation and one owner per installation.
Select services only after measuring requirements, transaction/recovery semantics,
and realistic costs. No database, orchestration service, or ingress is selected.

## Consequences

Docker/SQLite remains the operational baseline. A future stack must support
HTTPS, authentication, secure configuration, monitoring, and tested recovery.
Resolve budget, access, region, downtime, and acceptable data loss first. Rehearse
migration on copies and distinguish code rollback from data restoration. Adding
services requires a measured need, not an assumed serverless topology.

[Upcoming cloud work](../../TODO.md#cloud-work) and
[storage boundaries](0001-domain-and-storage-boundaries.md).
