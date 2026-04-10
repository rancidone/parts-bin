# Design Unit: UI Against The Enrichment Review Model

## Problem
The older UI doc described a simpler app: chat plus inventory. The current system has more operational state than that design accounted for:

- ingestion may create pending field-review proposals after the write
- users can manually refresh an existing part to request new proposals
- local JLC parts catalog readiness affects lookup coverage

The UI needs explicit boundaries between committed inventory, proposed updates, and environment setup.

## Surface Model

The top-level UI now has three surfaces:

- `Chat`
- `Inventory`
- `Settings`

Chat is still the default landing surface.

## Chat

Chat remains a unified input for ingestion and inventory questions.

- ingest confirmations describe the committed row or quantity increment
- query results show committed matches only
- pending review is not rendered as if it were already accepted inventory

If source-backed proposals exist, the user reviews them in Inventory rather than inside the chat thread.

## Inventory

Inventory is both the browse surface and the review surface.

Committed row data remains the main table view. When a part has pending proposals, the row should expose a review editor that lets the user:

- compare old and proposed values by field
- opt individual proposed fields in or out
- edit proposed values before acceptance
- accept the selected updates
- dismiss the proposal set

Acceptance writes both the updated fields and their provenance. Dismissal clears pending review state without mutating the committed row.

Inventory also owns manual refresh for existing parts with a valid `part_number`. Refresh generates proposals; it does not directly mutate the row.

## Settings

Settings is a lightweight operational surface for local catalog readiness.

In this slice it covers JLC parts catalog status:

- not configured
- missing
- downloading
- ready
- error

It also provides the user action to download or re-download the local catalog database.

## Export And Search Boundaries

Inventory export and client-side filtering continue to operate on committed inventory rows only. Pending review proposals and provenance metadata are not part of ordinary CSV export in this slice.

## Tradeoffs

Keeping review in Inventory instead of Chat preserves a cleaner conversational surface, but users may need to switch tabs after ingest to approve source-backed updates.

Adding Settings increases top-level navigation slightly, but it keeps operational download state out of both chat and the inventory table.
