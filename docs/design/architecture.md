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

Value canonicalization and stock identity belong in
[domain normalization](../../domain/normalization.py). Apply the same rules before
additions, edits, batch identity comparison, and accepted review writes. Storage
adapters persist supplied fields and match non-null filters by exact equality;
they must not invent units or apply their own normalization rules. Unknown or
incompatible suffixes remain uninterpreted. Exact manufacturer ordering suffixes
remain part of identity.

Historical edits may contain noncanonical passive spellings. Value search narrows
through the repository using the other filters, then compares values using domain
normalization for each candidate’s category. This preserves stored IDs, quantities,
timestamps, and evidence
without a startup rewrite or automatic merge. Duplicate decisions use exact
normalized identity, including unknown package values rather than treating them
as wildcards. Ambiguous historical identities require explicit resolution before
stock increments. Edits and batches check final identities for collisions before
writing. For the local inventory this trades broader candidate reads for safe
historical matching; indexed canonical fields would require a separately reviewed
migration. Accepted evidence retains its original field value and passage even
when the committed display value is canonicalized.

Conversation history has a separate injected repository contract because visible
events are published outside the inventory transaction. Disposable supplier caches
remain separate from authoritative storage. Select a cloud database and migration
procedure before adding its adapter; do not introduce a runtime picker or an
unimplemented cloud backend as part of this boundary.

Keep the conversation contract and provider-identity errors in the agent package;
the SQLite conversation adapter belongs in [db/conversations.py](../../db/conversations.py).
Entry points construct it explicitly. The adapter owns connections, schema setup,
event ordering, and atomic delivery deduplication; the gateway and runtime consume
only the contract. Preserve existing event sequences and historical provider
identity when changing adapters.

Supplied-source enrichment consumes [a separate cache contract](../../ingestion/cache.py).
The enrichment workflow owns identity, freshness durations, and cooldown timing;
the adapter owns atomic cache claims and serialization. The operator command
explicitly constructs [the SQLite cache](../../db/enrichment_cache.py), reusing the
local database file and existing cache rows without a migration. Sharing a file
does not give disposable cache rows authority over inventory, accepted evidence,
conversations, approvals, or mutation outcomes. Keep this contract separate from
the inventory unit of work; cache loss must not erase accepted provenance.

Scenario evaluation must seed and inspect state through the injected repositories
used by the runtime. A local storage factory can allocate isolated SQLite files;
the scenario runner must not open another database behind an adapter's back.
This lets future cloud adapters exercise the same domain and agent scenarios.
Concurrency, atomicity, and recovery also need adapter-specific tests.

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

Keep provider calls outside this transaction.

### Execution recovery

Persist the original request, provider response items, queued calls, and results
separately from visible history. Checkpoint model output before executing tools;
continue the same call queue after approval instead of asking a model to
reconstruct it. Images remain ephemeral. An interrupted initial photo request
needs resubmission if no derived response was checkpointed.

Give each execution step a stable operation identity. Commit ordinary additions
and stock increments with their saved outcomes in the inventory unit of work,
as with approved effects. Publish events with stable identities so replay does
not create duplicate conversation records. A worker lease fences checkpoint
updates and inventory mutations; it is not a scheduler or a guarantee about
external requests.

Treat interrupted retrieval conservatively: recover the inventory outcome and
report the incomplete lookup instead of automatically repeating a paid request.
An explicit lookup can start a fresh attempt. A model request interrupted before
its response checkpoint has an uncertain provider outcome; explicit execution
resume may issue that request again. These boundaries cannot promise exactly-once
provider billing. Cloud scheduling, browser recovery controls, cancellation,
and adapter-specific leases remain separate deployment work.

### Browser recovery

Keep only the current conversation ID in browser storage; conversation events
remain authoritative on the server and photos stay in component memory. The
[chat session](../../ui/src/agentSession.ts) serializes browser requests, deduplicates
and orders replayed events, and requires history refresh after network failure.
Restoration and refresh use the read-only event endpoint. Neither can issue new
model work, repeat a message, or infer approval from visible prose.

Resume is an explicit user action tied to the original execution ID. Refresh
history before resuming so a missed completion can prevent unnecessary work.
Visible events can identify unfinished requests but cannot prove that a worker
has stopped; the server lease remains authoritative. A photo request with no
visible derived response requires a fresh attachment. This is conservative and
may request a photo even if a response was checkpointed before publication.
Saved approval decisions resolve their corresponding controls; browser recovery
does not change server approval validation or mutation retry protection.

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

Separate application assembly from hosting. [application.py](../../application.py)
constructs the domain, approval engine, and gateway from supplied repositories,
supplier retrieval, and an OpenAI transport factory. It has no local configuration,
database filename, or FastAPI dependency. Worker entry points use these services
directly and close them when their host lifetime ends.

[server.create_app](../../server.py) builds the HTTP adapter from those services.
Each app owns its services and optional UI directory; requests use that app's
state rather than module-level installation state. FastAPI lifespan closes the
gateway on shutdown, following the
[lifespan guidance](https://fastapi.tiangolo.com/advanced/events/).
[local_app.py](../../local_app.py) is the local composition entry point: it loads
TOML configuration, initializes logging and SQLite adapters, and assembles the
HTTP host. Uvicorn invokes it explicitly as an application factory. Cloud
composition and scheduling remain separate work.

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
[the conversation contract](../../agent_runtime/store.py). CSV presentation lives
outside persistence in [inventory_export.py](../../inventory_export.py). Follow their tests for
behavioral assertions rather than maintaining a second specification here.
