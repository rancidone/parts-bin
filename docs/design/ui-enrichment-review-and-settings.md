---
status: stable
last_updated: 2026-04-12
---
# Design Unit: UI Against The Enrichment Review Model

## Problem

The older UI design assumed chat for ingest/query and a flat inventory browser. The current system has additional operational state: parts may have pending field-review proposals, users can manually refresh a part to fetch new proposals, and local JLC parts catalog status affects lookup coverage.

Without this design, UI implementation would have to guess where pending review appears, whether proposed values are treated as live inventory, and how operational setup surfaces to the user.

## Proposed Solution

The top-level UI exposes three surfaces: **chat**, **inventory**, and **settings**. Chat is the default landing surface. Inventory is where record-level review and editing happens. Settings is a lightweight operational panel, not a general administration console.

**Chat** — supports ingestion and inventory questions through one input. Ingest confirmations describe the committed row or quantity increment; they do not imply background enrichment proposals have been accepted. Query results show committed inventory matches only. Pending review state is not rendered as a chat result — if the user needs to evaluate proposals, the UI directs that work to inventory.

**Inventory review** — for any part with pending updates, the row visually indicates review state and lets the user: inspect old versus proposed values by field, opt individual fields in or out, edit proposed values before acceptance, accept the selected updates, or dismiss the proposal set. Acceptance writes both the chosen values and their provenance into durable storage. Dismissal clears pending proposal state without mutating the committed row.

**Manual refresh** — inventory allows a user to request a fresh enrichment proposal for an existing part with a valid part number. Refresh is proposal-generating, not directly mutating. Outcomes appear as pending updates when proposals exist; otherwise the row is left unchanged.

**Settings** — exposes JLC parts catalog readiness: whether local catalog support is configured, whether the database is missing, downloading, ready, or errored, and a user action to trigger download or re-download. This belongs in settings rather than chat because it is environment preparation, not inventory conversation.

**Export and search boundaries** — inventory export and client-side filtering operate on committed rows only. Pending review proposals and provenance metadata do not appear in ordinary export output.

## References

- `ingestion-enrichment-integration.md` — pending review model and proposal flow
- `server-api.md` — refresh, accept, dismiss, and pending endpoints

## Tradeoffs

Placing review in inventory rather than chat keeps chat simpler and avoids mixing committed facts with proposals, but users may need to switch surfaces after an ingest. Adding a settings surface introduces more top-level navigation, but keeps operational download state out of the conversational workflow and the inventory table.

## Readiness

High. The review surface, chat boundary, settings role, and committed-versus-proposed distinction are concrete enough for implementation.
