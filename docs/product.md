# Product boundaries

Parts Bin helps an electronics hobbyist identify parts from text or photos,
maintain stock, and find parts they already own. Ambiguous markings and incomplete
supplier descriptions need clarification, not invented facts.

## Ownership

One owner operates each independent installation and controls its data. Local
hosting remains supported; OpenAI requests still send bounded inputs to an
external provider. Cloud hosting requires authenticated access to UI and API.
Shared tenancy and synchronization across installations are outside scope.

## Inventory trust

The agent interprets requests; deterministic domain operations validate mutations
and decide matches. It receives narrow tools rather than a full inventory dump or
raw database access. Search answers come from committed stock and facts. Missing
evidence and pending proposals cannot establish electrical suitability.

A shared server-side policy requires approval for edits, deletion, bulk changes,
and enrichment acceptance. Clear additions and stock increments can follow a
complete instruction without redundant approval, subject to validation and
identity checks. Ambiguity requires clarification. Authentication does not replace
approval.

Enrichment proposes evidence-backed changes without altering quantity or silently
replacing committed facts. Old records remain valid without fabricated provenance.
User assertions must stay distinguishable from sourced facts.

Generic passive parts skip automatic manufacturer lookup. Assortment and kit
additions use `add_part` with `enrich=false` for each included type, preserving
user-supplied details without triggering supplier retrieval or extraction. The
assistant also honors explicit requests to skip enrichment. A skipped addition
can be enriched later with an explicit lookup request; skipping is not a failure
to retry.

## Conversation and cost

Keep provider failures visible and preserve historical conversation identity.
Distinguish committed outcomes from pending work. Send only the bounded context
needed for a request; keep credentials out of model input and private content out
of telemetry. See [storage and retention](adr/0005-storage-and-retention.md).

Keep the stack small and justify additional services with measured quality,
performance, or operating requirements. Count external AI/retrieval usage alongside
hosting costs. See [the decisions](adr/README.md).
