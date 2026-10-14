# 0040: Suggest stored nominal values without changing unit matching

**Status:** Suggestion scoping superseded by [0041](0041-cross-filtered-inventory-suggestions.md); stored spelling and input preservation retained.

## Context

Inventory supports unit-aware nominal filtering, but users may not know which
values are present. The package field already suggests committed spelling from
the inventory loaded for browsing.

## Decision

Use the same native suggestion mechanism for nominal values. List distinct,
nonempty committed values from the loaded inventory, narrowed only by category.
Retain stored notation and case, including meaningful SI prefixes; do not merge
or convert equivalent suggestions in the UI. All categories includes values
across inventory. Exclude pending proposals and blank values.

Allow selecting a stored value or typing an equivalent value. Keep the current
value and bookmark when category changes, even if it is no longer suggested.
Use the existing URL and shared domain comparison without changing their rules.

## Consequences

Users can discover available values without a model request or new endpoint.
Suggestions are independent of the other filters and page, and update with the
loaded committed inventory. Unit interpretation remains category-aware in the
domain. Equivalent stored notations can appear separately; changing category
can produce no matches until the user changes or clears the value.
