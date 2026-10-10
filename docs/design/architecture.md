# Architecture decisions

## One domain, several adapters

Keep inventory rules in a typed domain service. HTTP, the OpenAI agent, and UI code translate requests and results; they must not independently decide
identity, quantities, duplicate handling, or acceptance of enrichment.

This gives local and cloud deployments the same business rules and limits the
amount of behavior that must change when a transport or database changes. The
cost is explicit translation at adapter boundaries rather than convenient
provider-specific shortcuts.

Use one typed tool registry for agent access. Narrow tools let a model discover
relevant inventory without exposing a full database snapshot or arbitrary SQL.
Validation and approvals remain server-owned at every application entry point.

## One API-backed agent

Use the OpenAI Responses API for local and cloud deployments. Native function
calling connects the model to the typed registry without a subprocess, MCP server,
local inference adapter, or user-facing runtime selector. This reduces deployment
and lifecycle complexity at the cost of depending on an external API and its
usage charges. Hosting the application locally remains an option; offline model
execution is not part of this direction.

Keep visible events independent of provider response details so the UI can
understand messages, proposals, approvals, and failures. Preserve historical
provider identity: old conversations should remain readable, but must not silently
continue under different execution semantics.

Persistence of events is distinct from persistence of a provider's execution
state. A deployment must evaluate restart and interruption behavior explicitly;
replaying a transcript alone is not proof that an interrupted turn can resume.

### Responses API boundary

Application code executes validated functions and returns their outcomes to the
model. Keep optional patch fields optional: omitted means unchanged, while null
can mean clearing a field. Use non-strict provider schemas for this contract and
retain server-side validation, rather than forcing all properties to be present.
Keep tool calls sequential around approval boundaries. See the official
[function-calling guide](https://developers.openai.com/api/docs/guides/function-calling).

Carry the model's response items, including reasoning items, through a tool loop
instead of reconstructing only the function calls. Request non-stored responses
and keep that turn context application-owned. This reduces reliance on provider
session storage; it does not by itself make application execution durable or
establish a zero-retention policy. See
[reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning).

## Data ownership

Committed inventory is authoritative. User assertions, proposed enrichment, and
accepted provenance have different meanings and must remain distinguishable.
Search answers must come from committed inventory, not model memory or pending
proposals. Shared deterministic normalization keeps writes and searches aligned.

Local operation should be simple to install and recover. Cloud deployment must
externalize durable state where its execution model requires it, without making
cloud service APIs part of domain rules. Disposable supplier caches must never be
confused with inventory or conversation storage.

See [cloud hosting](cloud-hosting.md), [enrichment](enrichment.md), and
[product boundaries](product-contract.md) for the related decisions.

## Finding implementation details

Start with [domain/](../../domain/), [tools/registry.py](../../tools/registry.py),
[agent_runtime/](../../agent_runtime/), and [server.py](../../server.py).
Persistence definitions belong in [db/](../../db/) and
[conversation storage](../../agent_runtime/store.py). Follow their tests for
behavioral assertions rather than maintaining a second specification here.
