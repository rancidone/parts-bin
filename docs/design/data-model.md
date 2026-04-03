# Design Unit: Data Model & Persistence

## Problem
Define the storage schema, uniqueness constraints, normalization contract, and persistence layer responsibilities for the parts inventory.

## Storage Engine
**SQLite.** Single file, same host as the LLM, no server process. Appropriate for single-user, hundreds of SKUs.

## Schema — Single `parts` Table

```sql
CREATE TABLE parts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_category TEXT    NOT NULL,  -- free text, human-readable label
    profile       TEXT    NOT NULL CHECK (profile IN ('passive', 'discrete_ic')),
    value         TEXT,              -- normalized string; NULL for discrete_ic
    package       TEXT,              -- e.g. "0402", "SOT-23"; nullable
    part_number   TEXT,              -- manufacturer part number; NULL for passives
    quantity      INTEGER NOT NULL DEFAULT 0,
    manufacturer  TEXT,              -- from external lookup; nullable
    description   TEXT,              -- from external lookup; nullable
    created_at    TEXT    NOT NULL,  -- ISO 8601
    updated_at    TEXT    NOT NULL
);
```

`part_category` is free text and extensible. `profile` is a fixed two-value enum that drives all logic. New categories only require a profile assignment — no logic changes.

### Part Profiles

| Field | `passive` | `discrete_ic` |
|---|---|---|
| `part_category` | `"resistor"` / `"capacitor"` / `"inductor"` / … | `"transistor"` / `"diode"` / `"ic"` / `"mosfet"` / … |
| `value` | required, normalized | NULL |
| `package` | required | optional |
| `part_number` | NULL | required, duplicate key |
| `manufacturer` | NULL | optional (from lookup) |
| `description` | NULL | optional (from lookup) |

All logic (required fields, duplicate key, normalization) branches on `profile`, never on `part_category`.

## Uniqueness / Duplicate Detection

```sql
-- Passives: identity is category + value + package
CREATE UNIQUE INDEX uq_passive
    ON parts (part_category, value, package)
    WHERE part_number IS NULL;

-- Discretes/ICs: identity is manufacturer part number
CREATE UNIQUE INDEX uq_discrete
    ON parts (part_number)
    WHERE part_number IS NOT NULL;
```

Duplicate insert → persistence layer catches the constraint violation and issues `UPDATE quantity = quantity + N` instead.

## Normalization Contract

Normalization is applied by the persistence layer before any read or write reaches SQLite — not by callers. `value` is always stored in canonical normalized form. Queries normalize input before constructing the WHERE clause. See Query design unit for normalization rules and suffix table.

## Query Pattern — Null as Wildcard

Structured query fields map to WHERE conditions. NULL fields are omitted (wildcard, not exclusion):

```sql
-- { part_category: "resistor", value: "10k", package: null }
SELECT * FROM parts
WHERE part_category = 'resistor'
  AND value = '10k'
-- package omitted → matches any package
```

## Persistence Layer Responsibilities

- Normalize `value` before every write and before every read query
- Detect duplicate on insert; increment quantity rather than error
- Expose: `upsert(part)`, `query(structured_attrs)`, `list_all()`, `export_csv(rows)`
- No raw SQL outside the persistence layer

## Assumptions

- SQLite partial index support is sufficient (available since 3.8.0).
- `package` is nullable for `discrete_ic` — some parts may have unknown package at ingest time.
- `manufacturer` and `description` are always nullable — lookup failure is non-fatal.
- `export_csv` column order matches UI-defined schema: `part_category`, `value`, `package`, `quantity`, `part_number`, `manufacturer`, `description`.
- Profile assignment for new categories happens at LLM extraction time — the LLM outputs both `part_category` and `profile`.
