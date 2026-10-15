# Array inventory mutations and Boolean search

Bulk creation duplicate handling is superseded by
[0048](0048-domain-deduplication-for-bulk-creation.md).

## Context

Multi-value additions spent the bounded agent loop checking and adding one value
at a time. A failed request could leave only some values added. The existing
bulk editor applies one shared field set and supports per-record partial success;
it cannot express different quantities or metadata for each record.

## Decision

Expose bounded arrays for creating distinct records, incrementing explicit stock
targets, and updating each target with its own fields through the shared typed
registry and domain service. Each new array operation is atomic. Reject invalid
rows, duplicate targets, and identity conflicts without retaining earlier effects.
Creation never silently merges existing stock. Existing shared-field bulk editing
retains its documented partial-success behavior.

Persist addition receipts with the entire batch transaction. Route per-item edits
through one shared approval bound to every target's inventory and review snapshot;
commit the edits and approval result together. Replays return the saved outcome.
Bulk creation performs no implicit supplier requests: supplied metadata is retained,
and requested enrichment uses the existing source/review tools separately.

Allow bounded nested AND/OR expressions over the existing exact search filters,
combined with common filters and the per-record minimum stock threshold. Compile
and validate every branch before evaluating a single inventory read. Preserve
nominal-unit equivalence, exact ordering suffixes, stable pagination and unique
record results. Expression searches omit nominal alternatives so another value
cannot be mistaken for one of the requested identities. Electrical requirements
still require an explicit common category and their existing evidence contract.
Whole-list inventory checks likewise evaluate one committed inventory read.

## Consequences

An assortment can be checked in one tool call and created in another within one
user request, without raising the model-turn limit. Existing records require an
explicit choice to skip already-counted stock or increment additional stock.
A failed batch must be corrected and submitted again; successful batches must
not be repeated. Array bounds and expression depth/node limits bound work.
The expression evaluator processes stored candidates in application memory;
large-inventory query optimization remains separate from model-call efficiency.
