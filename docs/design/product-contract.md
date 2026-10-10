# Product boundaries

Parts Bin serves an electronics hobbyist who wants to add parts, identify them
from text or photos, maintain quantities, and find existing stock. Ambiguous
markings and incomplete supplier descriptions are ordinary inputs, not reasons
to invent missing facts.

## Ownership and deployment

Favor one owner per independent installation. Local-first operation means an
operator can retain a local deployment and control their data. Choosing cloud
hosting moves application data into that operator's cloud account; using the
OpenAI agent sends the bounded inputs needed for that request to its
provider. Local hosting alone does not make an external model call local.

Cloud access requires authentication. Single-owner scope does not mean public
unauthenticated access. Shared tenancy and inventory synchronization between
installations are outside this scope.

## Trust in inventory

A model may interpret an instruction, identify candidate parts, and summarize
retrieved records. Deterministic domain operations decide which records match,
validate mutations, and enforce duplicate and quantity rules. Models must not
receive a full inventory dump, raw database access, or an unrestricted action
interface.

Inventory search must support natural-language requirements over category-specific
specifications and available stock. The agent interprets requirements; validated
domain queries determine matches. Explain which committed facts satisfy each
constraint, and identify missing evidence separately. Unknown specifications,
pending proposals, or semantic similarity cannot establish that a part meets an
electrical requirement. See [specification search](flexible-part-model-and-identity.md#natural-language-requirements-search).

Keep one server-side approval policy. Reads do not need
mutation approval. Changes to identified committed records, deletion, bulk edits,
and acceptance of enrichment require explicit approval of the intended effect.
Clear additions and stock additions may follow a complete user instruction
without a redundant approval, subject to validation and duplicate handling.
Ambiguity requires clarification. Authentication and mutation approval solve
different problems; neither replaces the other.

An enrichment result is a proposal with evidence. It must not alter quantity or
silently replace committed facts. Old records without provenance remain valid;
do not fabricate evidence to make historical data appear complete.

## Conversations and privacy

Use the configured OpenAI API agent. Provider failures must remain visible.
Preserve historical conversation identity rather than silently switching older
threads to a different provider. Describe actual
committed outcomes separately from pending work or proposed metadata.

Send only the user input, bounded context, and tool information needed for the
agent request. Keep credentials outside model-visible content. Operational
telemetry should capture diagnostic metadata without prompts, photos, full
inventory records, or secrets. Provenance and conversation storage need their
own access and retention policies; redacted telemetry does not make all stored
application data non-sensitive.

## Scope discipline

Keeping costs down is a product goal. Use only services needed for the supported
workflows and deployment requirements. Prefer the existing storage and query
capabilities, and justify additional services with measured quality, performance,
or operational needs. Include infrastructure and external AI/retrieval usage in
the cost assessment. Preserve correctness, security, and recovery requirements
when reducing the stack.

Prefer direct inventory tools and source-backed enrichment over generic browsing,
shell, or database tools. Avoid speculative search systems, automatic runtime
fallbacks, and compatibility layers that perpetuate obsolete application paths.

See [architecture](architecture.md) for ownership rationale and
[enrichment](enrichment.md) for evidence and review decisions. Exact tool schemas
and approval classifications belong in [the registry](../../tools/registry.py)
and [approval code](../../agent_runtime/approval.py), not a copied list here.
