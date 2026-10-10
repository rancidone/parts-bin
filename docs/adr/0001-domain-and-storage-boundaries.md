# 0001: Keep domain rules independent of hosting and storage

Status: Accepted

Recorded: 2026-10-10 (existing project decision)

## Context

HTTP, agent, and UI entry points need the same inventory rules. Local SQLite
operation must survive changes to hosting or persistence without an unrelated
business-logic rewrite.

## Decision

Keep identity, stock, validation, and search in a typed domain service. Expose
narrow typed tools to the agent. Inject repository contracts; adapters own SQL,
connections, schema setup, and backend error translation. Assemble services
separately from HTTP startup, with SQLite selected explicitly by the local factory.

## Consequences

Adapters require explicit translation, but domain behavior can be tested and
reused independently. Future storage must meet the same contracts; a speculative
backend or runtime picker adds no value. Configuration remains outside images
and version control.

[Domain contracts](../../domain/repositories.py), [assembly](../../application.py),
[local factory](../../local_app.py), and [HTTP adapter](../../server.py).
