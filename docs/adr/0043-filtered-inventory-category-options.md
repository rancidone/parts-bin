# 0043: Derive Inventory category options from matching inventory

**Status:** Accepted

## Context

The category dropdown lists all loaded categories even when the other filters
exclude every record in them. The shared domain suggestion operation already
provides alternatives under the other structured filters and candidate text.

## Decision

Add category names to the existing read-only suggestion response. Apply text,
unit-aware nominal value, exact package and per-record minimum stock through
shared domain search, excluding the category filter itself. Return distinct
committed category names with their stored spelling. Ignore paging and pending
proposals as with the other lists.

Show these categories plus All categories and the current selected category.
Preserve a nonmatching selection and label it “no matching records” after a
successful response. Do not claim a mismatch during loading or failure. Keep
the existing bookmark and history behavior, including page reset on selection.

## Consequences

Users see categories that contain matching records while existing bookmarks
remain editable, including empty combinations. Category aliases and unit rules
remain in the domain; UI code only presents returned names. The operation adds
one shared inventory search to the existing request without new storage or
provider calls. Loading and recovery behavior applies to all three lists.
