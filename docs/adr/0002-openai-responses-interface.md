# 0002: Use one OpenAI Responses interface

Status: Accepted

Recorded: 2026-10-10 (existing project decision)

## Context

Multiple runtime backends and subprocess agents multiply authentication,
lifecycle, tool, and recovery paths. Local hosting concerns application ownership,
not offline model execution.

## Decision

Use the OpenAI Responses API for local and future cloud deployments. Carry
provider response items through the tool loop and request non-stored responses.
Keep execution context application-owned and validate tools server-side. Preserve
historical provider identity; retired threads must not continue through OpenAI.

## Consequences

The app depends on an external API and incurs usage charges even when hosted
locally. There is no automatic provider fallback. Application-owned context
supports recovery but does not promise zero provider retention.

[Transport](../../agent_runtime/transports.py) and [runtime](../../agent_runtime/runtime.py).
