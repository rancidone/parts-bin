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

This unit replaces the fixed-shape storage contract with a hybrid model:

- a small shared core for all parts
- flexible structured attributes for category-specific facts
- explicit identity rules that vary by part shape instead of pretending every part shares one schema

## Goals

- Preserve normalization where it is useful for deterministic matching
- Preserve user-entered display text instead of overwriting it with canonicalized storage forms
- Support arbitrary part shapes without inventing fake `value` or `part_number` fields
- Keep duplicate detection deterministic
- Keep inventory browsing and editing understandable in the UI
- Support grouped browsing by category / family in the inventory page

## Non-Goals

- Full ontology of every electronics category
- Semantic similarity search
- Automatic identity inference for every exotic part without user confirmation

## Proposed Storage Model

### Category Taxonomy

The system should distinguish between:

- `part_category`: the user-visible browse/group label
- `family`: the design-time structural shape used for identity and attribute expectations

`part_category` remains user-visible and inventory-facing.
`family` lives inside `attributes_json` (or may later be promoted to a dedicated column if query/index pressure justifies it).

The v1 browse taxonomy is:

- `passives`
- `protection`
- `connectors`
- `switches_relays`
- `diodes`
- `leds`
- `transistors`
- `analog_ic`
- `digital_logic`
- `microcontrollers`
- `memory`
- `interface_ic`
- `power_ic`
- `sensors`
- `modules_breakouts`
- `timing`
- `electromechanical`
- `hardware_misc`

These browse categories map into first-wave structural families:

- `passive`
- `connector`
- `semiconductor_discrete`
- `ic`
- `module`
- `switch`
- `protection`

### Core Columns

Every inventory row keeps a small shared core:

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

`mpn` replaces `part_number` as the top-level manufacturer part identifier. It is optional. It is not required for most categories.

`attributes_json` holds category-specific structured facts.

`identity_kind` and `identity_key` make duplicate detection explicit and deterministic.

## Attributes Model

`attributes_json` stores structured facts that do not belong in the shared core.

### Passive Example

```json
{
  "family": "passive",
  "passive_kind": "capacitor",
  "display_value": "100nF",
  "canonical_value": "100n",
  "package": "0603",
  "tolerance": "5%",
  "voltage_rating": "50V",
  "dielectric": "X7R"
}
```

### Connector Example

```json
{
  "family": "connector",
  "connector_type": "RCA",
  "gender": "female",
  "mount_style": "panel-mount",
  "termination": "solder",
  "positions": 1,
  "plating": "gold"
}
```

### IC Example

```json
{
  "family": "ic",
  "ic_kind": "op_amp",
  "package": "DIP-8",
  "logic_family": null,
  "series": "LM358"
}
```

### Switch Example

```json
{
  "family": "switch",
  "switch_type": "toggle",
  "mount_style": "panel-mount",
  "positions": 2,
  "poles": 1,
  "throws": 2
}
```

### Mechanical / Module Example

```json
{
  "family": "module",
  "module_type": "buck converter",
  "chipset": "TLV62565DBVR",
  "input_range": "2.7V-5.5V",
  "output_type": "adjustable"
}
```

## Identity Model

Duplicate detection should no longer branch on a hardcoded `profile` enum. Instead each stored row has an explicit identity strategy.

### Identity Kinds

- `mpn`
- `passive_value_package`
- `attributes_signature`
- `user_confirmed_freeform`

### Identity Rules

#### `mpn`

Use when a trustworthy manufacturer part number exists.

```text
identity_key = lower(trim(manufacturer)) + "::" + upper(trim(mpn))
```

If manufacturer is unknown but `mpn` is still strong and specific, `identity_key = upper(trim(mpn))` is acceptable.

This applies to ICs, discretes, regulators, modules, and some connectors/mechanical parts with a real vendor MPN.

#### `passive_value_package`

Use for canonical passives with no meaningful MPN requirement.

```text
identity_key = part_category + "::" + canonical_value + "::" + attributes.package
```

`canonical_value` comes from normalization.
`display_value` is preserved separately in attributes and shown back to the user.

#### `attributes_signature`

Use for non-passive categories without a trustworthy MPN but with a stable structured identity.

Examples:

- RCA female panel-mount solder connector
- panel-mount toggle switch with explicit poles / throws
- sensor breakout without trustworthy vendor MPN but with stable module/chipset identity
- toggle switch with poles/throws/mount style
- pin header with pitch/rows/pins

`identity_key` is built from a category-specific subset of normalized attributes in deterministic field order.

Connector example:

```text
connector::rca::female::panel-mount::solder::1
```

This is acceptable only when the attribute subset is explicit and stable.

#### `user_confirmed_freeform`

Fallback for items that do not yet have a safe deterministic identity rule.

The system stores a coarse identity key derived from category + description/package, but duplicate incrementing should not happen automatically unless the user explicitly confirms that the new item matches the existing row.

This is the safety valve for “all sorts of other shapes”.

## First-Wave Identity Coverage

The following categories should have explicit deterministic identity rules in v1.

### Passives

- browse category: `passives`
- underlying kinds include resistors, capacitors, ferrite beads, inductors

Rule:

- `identity_kind = passive_value_package` when a canonical value-like attribute exists plus package

### Connectors

- browse category: `connectors`
- includes RCA, headers, sockets, terminal blocks, board connectors, panel connectors, USB/power/RF connectors where no narrower v1 browse split is needed

Rule:

- `identity_kind = attributes_signature`
- signature built from connector type, gender when relevant, pitch when relevant, positions, mount style, termination, and package only when package is actually meaningful

### Semiconductor Discretes

- browse categories:
  - `leds`
  - `diodes`
  - `transistors`

Rule priority:

- `mpn` when available
- otherwise `attributes_signature` from semiconductor type + package + key electrical/color attributes

LED example:

```text
led::rgb::common_anode::5mm::through_hole
```

### ICs

- browse categories:
  - `analog_ic`
  - `digital_logic`
  - `microcontrollers`
  - `memory`
  - `interface_ic`
  - `power_ic`
  - `timing`

Rule priority:

- `mpn` when available
- otherwise conservative `user_confirmed_freeform`

Old through-hole ICs often do have a useful printed topmark / part number, so the system should strongly prefer MPN identity here.

### Modules

- browse categories:
  - `sensors`
  - `modules_breakouts`

Rule priority:

- `mpn` when available
- otherwise `attributes_signature` only when the module type + chipset + board form are explicit
- otherwise `user_confirmed_freeform`

### Switches

- browse category: `switches_relays`
- includes panel-mount switches, SMD switches, and relays for v1 browse purposes

Rule:

- `attributes_signature` from switch type, poles, throws, positions, mount style, actuator style, and package when relevant

### Protection

- browse category: `protection`
- includes polyfuses, classic fuses, TVS / ESD protection parts

Rule priority:

- `mpn` when available
- otherwise `attributes_signature` from protection type + package/form factor + key rated attributes

## Normalization Contract

Normalization still exists, but it is no longer the only representation.

### Passive Values

For passives, store both:

- `attributes.display_value`: exactly what the user entered or what the source showed
- `attributes.canonical_value`: normalized deterministic form used for identity and query matching

Examples:

- display: `100nF`, canonical: `100n`
- display: `10K`, canonical: `10k`
- display: `1uF`, canonical: `1u`

Manual edits must preserve `display_value`.
Duplicate detection and deterministic query matching use `canonical_value`.

### Other Attributes

Category-specific attributes may also have canonical forms if needed for identity construction, but the raw/display form should be preserved whenever the distinction matters.

`package` is one of those attributes. It is not a shared top-level field. It should live inside `attributes_json` and only be surfaced prominently in the UI for categories where it matters.

## Ingestion Impact

The LLM should no longer be required to force every part into:

- `profile`
- `value`
- `part_number`

Instead ingestion should produce:

- core fields
- structured attributes
- an identity proposal

The deterministic layer then decides whether:

- the identity is strong enough to auto-upsert
- clarification is needed because the attributes required for identity are missing
- the item should be stored under a conservative freeform identity path

### Clarification Behavior

Clarification prompts should ask for the missing identity facts, not for fields that only exist because of the old schema.

Bad clarification:

- “What is the value?”

Good clarification:

- “Is this RCA connector male or female?”
- “Is the mount style panel-mount or PCB?”
- “Does this have a manufacturer part number?”
- “What package is this capacitor in?”

## Query Impact

Query parsing should target:

- core fields
- attribute filters
- normalized passive canonical value when relevant
- category / family grouping

The exact-match lookup remains deterministic, but it should operate against:

- core columns
- selected extracted attributes from `attributes_json`
- `identity_key` where useful

No semantic search is added by this design.

## UI Impact

Inventory editing should present:

- core fields directly
- category-specific attributes as dynamic fields

The UI should not show empty irrelevant fields like `value`, `package`, or `mpn` for a connector row unless those facts actually exist in that row's attributes/core data.

For passives, the UI should show display value, not the canonical internal key.
For package-bearing categories, the UI may promote `attributes.package` into a normal visible column, but that is a presentation decision, not a storage-level top field.

### Inventory Browse Structure

The inventory page should support category-oriented browsing rather than one undifferentiated flat list.

Minimum behavior:

- group rows by `part_category`
- allow collapse / expand by category
- keep client-side text filter across all groups
- keep sortable rows within a group
- render collapsed rows with a stable three-field summary contract

The top-level browse experience should feel like:

- Passives
- Protection
- Connectors
- Switches / Relays
- Diodes
- LEDs
- Transistors
- Analog IC
- Digital Logic
- Microcontrollers
- Memory
- Interface IC
- Power IC
- Sensors
- Modules / Breakouts
- Timing
- Electromechanical
- Hardware / Misc

The inventory page should group directly by `part_category` for v1.
`family` remains an internal structural concept for identity and attribute expectations, not the primary browse grouping.

### Collapsed Row Summary Contract

Every collapsed inventory row should show exactly three browse fields:

- `quantity`
- `identity`
- `value`

These are presentation fields, not a direct mirror of storage columns.

#### Quantity

Always the stock count.

#### Identity

The main human-recognizable label for the part.

Examples:

- `LM358`
- `2N7002`
- `RCA female panel-mount`
- `toggle switch SPDT`
- `HC-SR04`

Priority order for deriving `identity`:

1. `mpn` when it is the most recognizable identifier
2. category-specific identity label from attributes
3. concise description fallback

#### Value

The most useful secondary discriminator for quick scanning within a category.

Examples:

- passive: `100nF`, `10k`, `600 ohm @ 100MHz`
- connector: `panel-mount`, `2x8 2.54mm`, or blank if no good secondary value exists
- IC: `DIP-8`, `SOIC-14`, or subtype such as `dual op amp`
- module: chipset, interface, or package/form factor hint
- switch: mount style, poles/throws, or actuator style

`value` here is a browse slot, not the old schema field. It must be derived from category/family-aware formatting logic.

### Expanded Row Contract

Expanded rows should show:

- core fields
- all category-specific attributes
- editable controls

The expanded view is the canonical place for full detail and manual correction.

## Migration Strategy

This is a schema migration, not a patch.

### Phase 1

- Add `mpn`, `attributes_json`, `identity_kind`, `identity_key`
- Keep existing columns temporarily
- Backfill current rows into the new shape

### Phase 2

- Move ingestion, edit, and query paths to the new model
- Derive passive `canonical_value` from the existing normalized `value`
- Derive passive `display_value` from either prior user text where available or the canonical value as a fallback

### Phase 3

- Remove `profile`
- Remove top-level semantics that require `value` and `part_number` for every row
- Optionally deprecate top-level `value` entirely once UI/query paths no longer depend on it

## Tradeoffs

### Benefits

- Stops forcing fake values into irrelevant fields
- Preserves manual input formatting
- Supports connectors and mechanical parts cleanly
- Keeps deterministic duplicate detection
- Gives a path for additional part shapes without recurring schema crises

### Costs

- Harder query implementation than a fixed flat schema
- More migration work
- More careful UI rendering/editing required
- Identity design becomes explicit product logic instead of hiding inside one enum

## Open Questions

- How much attribute-specific filtering must the first inventory UI support versus a smaller “core only + details” presentation?

## Readiness

Medium readiness.

The design direction is concrete enough to stop patching the current two-shape schema, but it is not implementation-ready until the follow-on units are revised:

- ingestion
- query
- inventory editing / browse UI
- migration plan details