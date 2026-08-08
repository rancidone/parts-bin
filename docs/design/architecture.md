---
status: stable
last_updated: 2026-08-08
---
# Parts Bin architecture

Parts Bin is a single-user, local-first SQLite inventory application. Its only
conversational path is the agent gateway: a thread selects `codex`, `openai`,
or `local`, then receives one normalized event stream for messages, tools,
approvals, errors, and completion.

The gateway owns thread/runtime selection and durable visible events. Agent
runtimes own provider transport and bounded tool loops. The registry is the
single typed tool contract and maps validated calls to the domain service. The
domain service owns inventory rules and SQLite transactions. The MCP server is
the Codex projection of that same registry.

Models discover inventory with narrow tools. They never receive an inventory
dump, direct database access, credentials, or unrestricted mutation surface.
The approval engine is server-owned and applies equally to every runtime.

See [the product contract](product-contract.md) for the complete tool,
approval, privacy, and runtime-selection policy.
