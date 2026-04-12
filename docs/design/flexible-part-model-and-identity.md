---
status: draft
last_updated: 2026-04-12
---
# Design Unit: Flexible Part Model & Identity

## Problem

The current schema assumes every part fits one of two shapes:

- passive: `value + package`
- discrete / IC: `part_number`

That assumption is already failing. Connectors, switches, mechanical parts, modules, kits, and many AliExpress listings do not have a meaningful passive `value` or a manufacturer `part_number`. The current model forces those parts into the wrong fields, which creates bad extraction, bad edits, misleading chat responses, and brittle duplicate detection.

## Proposed Solution

Replace the fixed two-shape storage contract with a hybrid model:

- a small shared core for all parts
- flexible structured attributes in `attributes_json` for category-specific facts
- explicit identity rules that vary by part shape

### Core Schema

```sql
CREATE TABLE parts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    part_category     TEXT NOT NULL,
    quantity          INTEGER NOT NULL DEFAULT 0,
    manufacturer      TEXT,
    mpn               TEXT,
    description       TEXT,
    attributes_json   TEXT NOT NULL,   -- structured per-part attributes
    identity_kind     TEXT NOT NULL,   -- how this row dedupes
    identity_key      TEXT NOT NULL,   -- canonical deterministic key
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
```

`mpn` replaces `part_number` as the manufacturer part identifier — optional for most categories. `profile` is removed; structural shape is implicit in `attributes_json` via a `family` field.

### Category Taxonomy

`part_category` remains the user-visible browse/group label. `family` lives inside `attributes_json` and drives identity and attribute expectations.

Browse categories:

- `passives`, `protection`, `connectors`, `switches_relays`, `diodes`, `leds`, `transistors`, `analog_ic`, `digital_logic`, `microcontrollers`, `memory`, `interface_ic`, `power_ic`, `sensors`, `modules_breakouts`, `timing`, `electromechanical`, `hardware_misc`

Structural families:

- `passive`, `connector`, `semiconductor_discrete`, `ic`, `module`, `switch`, `protection`

### Attributes Model

`attributes_json` stores category-specific structured facts. Examples:

**Passive** — `family`, `passive_kind`, `display_value`, `canonical_value`, `package`, `tolerance`, `voltage_rating`, `dielectric`

**Connector** — `family`, `connector_type`, `gender`, `mount_style`, `termination`, `positions`, `plating`

**IC** — `family`, `ic_kind`, `package`, `logic_family`, `series`

**Switch** — `family`, `switch_type`, `mount_style`, `positions`, `poles`, `throws`

**Module** — `family`, `module_type`, `chipset`, `input_range`, `output_type`

### Identity Model

Each row has an explicit `identity_kind` and a derived `identity_key` used for deterministic duplicate detection.

| Kind | When to use | Key construction |
|---|---|---|
| `mpn` | Trustworthy manufacturer part number exists | `lower(manufacturer) + "::" + upper(mpn)` |
| `passive_value_package` | Canonical passives without MPN requirement | `part_category + "::" + canonical_value + "::" + package` |
| `attributes_signature` | Stable structured identity without MPN | Deterministic subset of normalized attributes |
| `user_confirmed_freeform` | No safe deterministic identity rule yet | Coarse key; auto-increment only with user confirmation |

First-wave identity coverage by category:

- **Passives** → `passive_value_package`
- **Connectors** → `attributes_signature` (type, gender, pitch, positions, mount style, termination)
- **Discretes / LEDs** → `mpn` preferred; `attributes_signature` fallback
- **ICs** → `mpn` strongly preferred; `user_confirmed_freeform` fallback
- **Modules / Sensors** → `mpn` preferred; `attributes_signature` when module type + chipset are explicit; `user_confirmed_freeform` otherwise
- **Switches** → `attributes_signature` (type, poles, throws, mount style, actuator style)
- **Protection** → `mpn` preferred; `attributes_signature` fallback

### Normalization Contract

For passives, store both representations:

- `attributes.display_value` — exactly what the user entered (e.g. `100nF`, `10K`)
- `attributes.canonical_value` — normalized form used for identity and query matching (e.g. `100n`, `10k`)

Duplicate detection and deterministic query matching use `canonical_value`. Manual edits preserve `display_value`. Other attributes may also have canonical forms where needed for identity construction.

### Ingestion Impact

The LLM no longer forces every part into `profile` / `value` / `part_number`. Instead it produces core fields, structured attributes, and an identity proposal. The deterministic layer then decides whether to auto-upsert, request clarification, or fall back to `user_confirmed_freeform`.

Clarification should ask for missing identity facts, not for fields that only exist because of the old schema. Good: "Is this RCA connector male or female?" Bad: "What is the value?"

### Query Impact

Query parsing targets core fields, attribute filters, and normalized passive canonical value. The exact-match lookup remains deterministic, operating against core columns, selected extracted attributes from `attributes_json`, and `identity_key` where useful. No semantic search is added.

### UI Impact

The inventory page groups rows by `part_category` with collapse/expand. Each collapsed row shows three browse fields:

- **Quantity** — stock count
- **Identity** — main human-recognizable label (MPN, category-specific label, or description fallback)
- **Value** — most useful secondary discriminator (passive value, package, subtype, etc.)

Expanded rows show all core fields and category-specific attributes with editable controls. The UI does not show empty irrelevant fields (e.g. no `value` column for a connector row).

### Migration Strategy

**Phase 1** — Add `mpn`, `attributes_json`, `identity_kind`, `identity_key`. Keep existing columns. Backfill current rows.

**Phase 2** — Move ingestion, edit, and query paths to the new model. Derive passive `canonical_value` and `display_value` from existing normalized `value`.

**Phase 3** — Remove `profile` and top-level `value` / `part_number` semantics once all paths no longer depend on them.

## References

- `ingestion-enrichment-integration.md` — ingestion path changes required
- `query-runtime-alignment.md` — query path changes required
- `ui-enrichment-review-and-settings.md` — UI changes required

## Tradeoffs

Flexible attributes stop forcing fake values into irrelevant fields, preserve manual input formatting, and give a clean path for new part shapes without recurring schema crises. The costs are a harder query implementation, more migration work, more careful UI rendering, and explicit identity logic that was previously hidden inside a two-value enum.

## Readiness

Medium. The design direction is concrete enough to stop patching the current schema, but not implementation-ready until ingestion, query, UI, and migration follow-on units are revised to match.

Open question: how much attribute-specific filtering must the first inventory UI support versus a simpler "core only + details" presentation?
