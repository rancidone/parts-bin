# 0041: Cross-filter Inventory suggestions through shared domain search

**Status:** Text-query scoping superseded by [0042](0042-text-aware-inventory-suggestions.md); other decisions retained; supersedes suggestion-scoping decisions in [0039](0039-inventory-package-suggestions.md) and [0040](0040-inventory-nominal-value-suggestions.md).

## Context

Category-only suggestions can offer values and packages with no stock or no
matching combination. Narrowing packages by nominal value requires the domain’s
unit-aware comparison rather than literal comparison in the UI.

## Decision

Serve both suggestion lists from a read-only domain operation and HTTP endpoint.
Nominal-value suggestions apply category, exact package and per-record minimum
stock, excluding the nominal-value filter itself. Package suggestions apply
category, unit-aware nominal value and per-record stock, excluding the package
filter itself. Reuse typed domain search for both. Text and page are excluded.

Return distinct, nonempty committed spelling in sorted lists. Preserve case,
notation and pending-review separation. Do not automatically change inputs when
suggestions change; continue storing the same criteria in bookmark URLs.

Debounce requests, discard obsolete responses, clear previous options during
loading or failure, and offer retry. Refresh suggestions when committed inventory
or quantity state changes.

## Consequences

Users see alternatives that exist under the other structured filters, including
recovery options for empty combinations. Unit interpretation remains in the
domain. This adds a read-only request and two inventory searches, without new
storage, provider calls or mutations. Suggestions reflect current server data
and do not imply interchangeable parts or aggregated stock.
