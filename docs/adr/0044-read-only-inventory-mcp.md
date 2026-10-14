# 0044: Expose stored inventory through read-only MCP

**Status:** Accepted

## Context

External agents need to reason about available stock and its recorded evidence.
The removed MCP server used older persistence and mutation paths. The current
domain service and typed tool registry already define inventory identity,
availability, qualified electrical searches and bounded pagination.

## Decision

Expose an optional authenticated Streamable HTTP endpoint at `/mcp` using the
official Python MCP SDK. Project an explicit read-only allowlist from the shared
registry, preserving its schemas, descriptions, validation and domain execution.
Reject every other tool name at invocation, independently of advertised tools or
client read-only annotations. Return structured outcomes and a text representation.

Use stateless JSON responses: reads need no conversational sessions, model
submission or durable agent delivery. MCP neither writes stock nor fetches new
supplier evidence. Part-scoped evidence can include clearly labeled pending
reviews and user assertions; their existing confirmation rules still apply.

Enable the endpoint explicitly with a dedicated installation bearer token in
protected configuration. Use the existing HTTPS ingress, validate its Host and
Origin, bound request bodies and keep the endpoint disabled without configuration.
One token represents the installation owner; this adds no tenancy or shared-user
authorization. Clients must support supplying an Authorization header; this
endpoint does not implement OAuth discovery or interactive client registration.

## Consequences

External agents can discover, search and check BOM availability without provider
charges or alternate business rules. Stock remains a live observation, not a
reservation. MCP access shares the owner's visibility; protecting, rotating and
revoking its token is an operator responsibility. Token removal/rotation requires
HTTP restart. The application's own agent remains on OpenAI Responses, and
historical conversation identity is unaffected. HTTP deployment adds one SDK
dependency and no new service, port, database or migration.
