# 0036: Browse candidate inventory through the shared domain search

Status: Accepted

## Context

Chat has multi-term candidate discovery, but Inventory applies a separate local
substring filter. Finding parts by manufacturer, package and stock should not
require a model request or divergent matching rules in the browser.

## Decision

Expose a read-only inventory search GET using the typed domain candidate page.
Use the domain inventory page when the text query is empty. Support category and
per-record minimum stock filters, including category/stock-only searches. Retain
bounded pages, full result counts, part identities and explicit candidate labels.
Search committed fields; pending review text and electrical suitability remain
outside candidate discovery.

Replace local Inventory text matching with this endpoint. Debounce input, abort
superseded reads and ignore late results. Clear stale results while a new query,
page or inventory revision is pending. Show search errors separately from an empty
result. Reset paging when filters change and bypass filters for a direct part link.
Keep the existing editing, approvals, electrical search and full-inventory browsing
flows. Treat filtered sorting and export as operations on the visible page.

## Consequences

Inventory discovery needs no agent configuration, provider request, supplier call,
schema migration or new service. HTTP and agent adapters share domain matching,
identity preservation and stock validation. Category choices come from committed
inventory. Edits and stock adjustments invalidate filtered results for a fresh read;
paging reads current data and should restart after concurrent inventory changes.
