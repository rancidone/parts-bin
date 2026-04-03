# Design Unit: UI

## Problem
Single-user responsive web app supporting ingest (photo/text) and query (natural language) in a unified conversational interface, plus a browsable inventory view with export. Usable on mobile and desktop, served from the same host as the local LLM.

## Framework & Tooling

**React + TypeScript** with **Vite** as the build tool. Vite gives fast dev-server HMR and a simple build step.

- `npm create vite@latest` → React + TypeScript template
- No UI component library — plain CSS or CSS modules. Keeping dependencies minimal makes it easier to understand what React itself is doing vs. what a library is doing.
- Fetch via the browser `EventSource` API for SSE (chat responses) and plain `fetch` for `GET /inventory`.
- Vite dev server proxies `/chat` and `/inventory` to the FastAPI backend during development — no CORS issues locally.

## Layout

Two surfaces: **Chat** and **Inventory Browser**. Accessible via a top-level toggle/tab — single route with panel swap, or two routes. Chat is the default landing surface.

```
┌─────────────────────────────┐
│  [Chat]  [Inventory]        │  ← top-level nav
├─────────────────────────────┤
│                             │
│   (active surface)          │
│                             │
├─────────────────────────────┤
│  [📎] [text input      ] ▶  │  ← chat input (visible on Chat surface only)
└─────────────────────────────┘
```

## Chat Surface

Single-column message thread. Input fixed at bottom. Intent (ingest vs. query) inferred from input — no mode switching.

On mobile: `📎` triggers camera or file picker.
On desktop: file picker; drag-drop onto input acceptable.

### Message Types

| Turn | Appearance |
|---|---|
| User text | plain bubble |
| User photo | thumbnail + optional caption |
| System query result | structured card (see below) |
| System ingest confirmation | structured card: extracted record + "added" or "incremented N→M" indicator |
| System clarification prompt | plain text naming exactly which fields are missing, with partial extracted record shown |
| System not-found | plain text |

### Query Result Card
Displays matched parts with quantity and key specs. Includes a **Export BOM** button that downloads the result set as CSV.

BOM CSV columns: `part_category`, `value`, `package`, `quantity`, `part_number` (if applicable), `manufacturer`, `description` (spec fields from external lookup where available).

### Clarification Flow
System shows the partial extracted record alongside the prompt — user sees what was captured and what is missing. User responds in the next chat turn. No modal or form overlay.

## Inventory Browser Surface

Flat list of all inventory records. Sortable by part category, value, package. Searchable by text filter (client-side, no LLM involved).

Controls:
- **Export CSV** button — downloads full inventory as CSV with the same column schema as BOM export.
- Per-row: part details inline; no separate detail view required for v1.

CSV columns (both inventory export and BOM): `part_category`, `value`, `package`, `quantity`, `part_number`, `manufacturer`, `description`.

## Constraints
- Responsive: single codebase, no separate mobile build.
- No auth, no multi-user state.
- Served from same host as LLM — Vite dev server proxies `/chat` and `/inventory` to FastAPI during development.
- Client-side search/filter in inventory browser (no extra server round-trip for browsing).

## Assumptions
- BOM export and full inventory export share the same CSV column schema.
- Inventory browser sort/filter is client-side; the full inventory (hundreds of SKUs) fits in memory without pagination for v1.
- Camera access on mobile requires HTTPS or localhost — deployment must account for this.
