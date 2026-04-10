# UI Against The Enrichment Review Model

## Scope

This unit defines the user-facing UI behavior required by the structured enrichment attempt model. It covers chat, inventory review, manual refresh, acceptance and dismissal of pending updates, and the settings surface for local catalog preparation.

This unit does not redesign the visual language of the application.

## Problem

The older UI design assumed a simpler world: chat for ingest/query and a flat inventory browser. The current implementation now has additional operational state:

- saved parts may have pending field-review proposals
- users may manually refresh a part to fetch new proposals
- local JLC parts catalog status affects available lookup coverage

Without a design update, UI implementation would have to guess where pending review appears, whether proposed values are treated as live inventory, and how operational setup surfaces to the user.

## Design Goals

- Keep chat focused on committed user-visible outcomes.
- Make pending enrichment proposals visible in inventory where users already inspect records.
- Separate committed inventory fields from proposed source-backed updates.
- Provide a small operational settings surface for local catalog readiness.

## Surface Model

The top-level UI should expose three surfaces:

- chat
- inventory
- settings

Chat remains the default landing surface. Inventory is where record-level review and editing happens. Settings is a lightweight operational panel, not a general administration console.

## Chat Behavior

Chat continues to support ingestion and inventory questions through one input surface.

For ingestion, the immediate confirmation should describe the committed row or quantity increment. It should not pretend that background enrichment proposals, if any, have already been accepted.

For query, chat should present only committed inventory matches.

Pending review state should not be rendered as if it were a confirmed chat result. If the user needs to evaluate source-backed proposals, the UI should direct that work to the inventory surface.

## Inventory Review Behavior

Inventory is the review surface for enrichment proposals.

For any part with pending updates, the row should visually indicate review state and allow the user to:

- inspect old versus proposed values by field
- opt individual proposed fields in or out
- edit proposed values before acceptance
- accept the selected updates
- dismiss the proposal set

Acceptance must write both the chosen values and their provenance into durable storage. Dismissal must clear the pending proposal state without mutating the committed row.

## Manual Refresh

Inventory should allow a user to request a fresh enrichment proposal for an existing part with a valid part number.

Refresh is proposal-generating, not directly mutating. The UI should therefore show refresh outcomes as pending updates when proposals exist and otherwise leave the row unchanged.

## Settings Surface

Settings should expose only operational controls required by the current model.

The concrete requirement in this slice is JLC parts catalog readiness:

- whether local catalog support is configured
- whether the database is missing, downloading, ready, or errored
- a user action to trigger download or re-download

This belongs in settings rather than chat because it is environment preparation, not inventory conversation.

## Export And Search Boundaries

Inventory export and client-side filtering should continue to operate on committed inventory rows only.

Pending review proposals and provenance metadata should not silently appear in ordinary export output for this slice.

## Tradeoffs

Placing review in inventory instead of chat keeps chat simpler and avoids mixing committed facts with proposals, but it does mean users may need to switch surfaces after an ingest.

Adding a settings surface introduces more top-level navigation, but it keeps operational download state out of the conversational workflow and out of the inventory table itself.

## Readiness

This unit is at high readiness. The review surface, chat boundary, settings role, and committed-versus-proposed distinction are concrete enough for implementation.