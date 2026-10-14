# 0039: Suggest exact package spelling from committed inventory

**Status:** Suggestion scoping superseded by [0041](0041-cross-filtered-inventory-suggestions.md); stored spelling and input preservation retained.

## Context

Exact package matching preserves distinct stored identities, but users need to
know the spelling in their inventory to apply it. Inventory already loads the
committed records for browsing and editing.

## Decision

Attach native package suggestions to the existing exact package input. Derive
distinct, nonempty package names from the loaded committed inventory, narrowed
by the selected category, and sort them without changing case or punctuation.
With no category selected, include packages across the inventory. Other search
criteria and paging do not narrow suggestions; pending proposals are excluded.

Keep the current input and its bookmark value when category changes, even if it
is no longer suggested. Users may choose a stored spelling or type their own.
The shared domain search remains responsible for exact filtering.

## Consequences

No new endpoint or package alias rules are needed. Suggestions use the existing
inventory load and update with committed edits, deletions and refreshes. They
reflect that loaded inventory until refreshed. A package outside the selected
category can still produce an empty result instead of being silently replaced.
