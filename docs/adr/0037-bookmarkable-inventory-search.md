# 0037: Restore Inventory search criteria from bookmarkable URLs

Status: Accepted

## Context

Direct Inventory candidate search uses the shared domain service, but its browser
filters and page are held in component state. A bookmark, reload or history change
cannot reproduce the search criteria.

## Decision

Make the hash route's query string the source of Inventory search state. Store
`query`, `category`, `minimum_quantity` and the one-based `page`; omit defaults.
Encode literal punctuation, Unicode and whitespace through URLSearchParams. Keep
50 records per filtered page and translate the page to the existing API offset.
Reject unsafe page arithmetic by falling back to page 1. Preserve invalid stock
and oversized text for visible validation instead of silently changing the query.

Read route state on every navigation, including reload and Back/Forward. Use
synchronous hash-router updates so controlled search inputs do not lose rapid
keystrokes to deferred navigation renders. Push
history for category changes, paging and clearing filters. Group consecutive edits
in the same text field into one history entry: push its first edit and replace
subsequent edits until blur or navigation. Reset the page when filters change.
Abort superseded reads using the existing search adapter; history navigation only
performs read-only discovery, never agent work, approvals or mutations.

Keep direct part links separate from candidate filtering. If a bookmark points
past current results, show an unavailable-page message with explicit first-page
recovery. Continue using the shared domain API without another persistence store.
Electrical filter forms, sorting and bulk-selection state are outside the saved
candidate-search criteria.

## Consequences

Search links work across reloads and tabs without local storage or schema changes.
They restore criteria and fetch current inventory, preserving ordering suffixes
and stock semantics; they do not preserve a historical stock snapshot. Existing
query constraints still gate requests, and malformed input receives safe paging
defaults or clear validation. Future direct-search filters must use the same
route-state boundary to retain bookmark and navigation behavior.
