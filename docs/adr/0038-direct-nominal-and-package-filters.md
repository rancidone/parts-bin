# 0038: Filter Inventory by nominal value and exact package

**Status:** Accepted

## Context

Literal candidate terms cannot find equivalent nominal values stored in different
unit notation. Inventory also needs package filtering that preserves distinct
stored package identities. These criteria must survive bookmarks and navigation.

## Decision

Expose nominal value and exact package alongside Inventory’s existing criteria.
Pass both through the typed domain search. Values use each committed part’s
category: passive values share the existing unit-aware comparison; other values
compare exactly. Category remains optional. Package uses the existing exact
comparison after trimming input; case and punctuation remain meaningful.

Persist the criteria as `value` and `package` in the Inventory URL. Changes reset
the page, and reload and Back/Forward restore the controls. Combine them with
literal candidate terms and per-record minimum stock.

Page only records matching the supplied filters in direct Inventory search.
Keep nearby nominal alternatives in the existing agent search response, where
their differences are explained, rather than letting them affect strict paging.

## Consequences

The UI and HTTP adapter do not interpret units or introduce package aliases.
Equivalent nominal notation is discoverable without a model request. Users can
select a category to clarify bare values, and must supply the stored spelling
for an exact package. Pending changes are excluded and searches remain read-only.
