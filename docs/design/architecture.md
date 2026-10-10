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

### Repository boundary

Inject storage into the domain service instead of making inventory rules open a
database or select a backend. Typed repository contracts live in
[domain/repositories.py](../../domain/repositories.py); the local implementation
is [the SQLite adapter](../../db/repository.py). Application entry points choose
the adapter explicitly. SQL, connection lifecycle, schema initialization, and
backend exception translation belong in storage adapters.

The boundary includes a unit of work shared by inventory, pending evidence, and
approval outcomes. A cloud implementation must preserve the same conditional
write, atomic commit, rollback, and retry semantics. A CRUD interface that stores
inventory and operation outcomes independently would lose that guarantee.
Backend-specific constraints must become repository errors before reaching
domain rules. Keep existing SQLite data and schema readable during this refactor.

Conversation history has a separate injected repository contract because visible
events are published outside the inventory transaction. Disposable supplier caches
remain separate from authoritative storage. Select a cloud database and migration
procedure before adding its adapter; do not introduce a runtime picker or an
unimplemented cloud backend as part of this boundary.

### Approval continuation

Keep approval requests, final decisions, and mutation outcomes in the inventory
database so an approved effect and its saved result can share one transaction.
Conversation events may live separately; their publication is outside that
transaction and must not be treated as proof that an operation was uncommitted.
Each proposal gets a unique operation ID scoped to its conversation, even when
another proposal has identical arguments. Duplicate approval delivery returns
the saved outcome rather than repeating the mutation.

Bind proposals to a snapshot of their target records and pending evidence,
including record timestamps. Revalidate under the write transaction before the
effect. A changed target requires a fresh proposal and approval. This conservative
policy also rejects intervening quantity changes instead of silently applying
an old review to new stock state.

Keep provider calls outside this transaction. Approval continuation covers the
approval-gated tools; it does not establish recovery or deduplication for an
entire model turn, ordinary additions, enrichment retrieval, or event publication.

### Authoritative records

Committed inventory is authoritative. User assertions, proposed enrichment, and
accepted provenance have different meanings and must remain distinguishable.
Search answers must come from committed inventory, not model memory or pending
proposals. Shared deterministic normalization keeps writes and searches aligned.

Local operation should be simple to install and recover. Cloud deployment must
externalize durable state where its execution model requires it, without making
cloud service APIs part of domain rules. Disposable supplier caches must never be
confused with inventory or conversation storage.

## HTTP and worker execution

Keep FastAPI as the HTTP adapter while evaluating serverless hosting. Replacing
the routing library does not solve durable state, approval continuation, or
retry semantics. Preserving the API and local workflow avoids an unrelated
rewrite while those constraints are resolved.

Enrichment processing should be callable without FastAPI, so an operator command
and a future queue worker can share retrieval/extraction behavior. A cloud HTTP
adapter remains an open choice: evaluate authentication, streamed responses, and
request limits before selecting a Lambda integration. Process memory and local
temporary storage do not provide durable cloud jobs or inventory.

See [cloud hosting](cloud-hosting.md), [enrichment](enrichment.md), and
[product boundaries](product-contract.md) for the related decisions.

## Finding implementation details

Start with [domain/](../../domain/), [tools/registry.py](../../tools/registry.py),
[agent_runtime/](../../agent_runtime/), and [server.py](../../server.py).
Persistence definitions belong in [db/](../../db/) and
[conversation storage](../../agent_runtime/store.py). Follow their tests for
behavioral assertions rather than maintaining a second specification here.
