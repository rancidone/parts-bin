# Design Unit: Ingestion

## Problem
User adds a part from a photo, text, or both. The system must extract a structured record, detect when it is incomplete, ask only what is needed, handle duplicates, and commit deterministically.

## Required Fields by Part Category

| Category | Required fields |
|---|---|
| Passive (R, C, L) | `part_category`, `profile`, `value`, `package`, `quantity` |
| Discrete / IC | `part_category`, `profile`, `part_number`, `quantity` |

`part_category` and `profile` are both required for every record. `profile` (`passive` | `discrete_ic`) drives all logic branching. Fields are represented as `null` when absent or unresolvable; no separate confidence score is used.

## Flow

```
input (photo / text / both)
  └─ LLM extraction → structured record (null for any field that cannot be resolved)
       └─ completeness check (all required fields non-null?)
            ├─ INCOMPLETE → conversational clarification prompt (show partial record, name missing fields)
            │    └─ user fills gaps → re-check (loop until complete)
            └─ COMPLETE
                 ├─ duplicate check
                 │    ├─ MATCH (passive: category+value+package; discrete/IC: part_number)
                 │    │    └─ increment quantity → write
                 │    └─ NO MATCH → new entry
                 │         ├─ passive → write directly
                 │         └─ discrete/IC → external spec lookup → merge specs → write
```

## Boundaries

- LLM is responsible for extraction only. It emits `null` for any field it cannot resolve. It does not decide whether to commit.
- Completeness check is deterministic: all required fields non-null → complete.
- Duplicate match is deterministic: exact key match (category+value+package for passives; part_number for discrete/IC).
- External lookup is for spec enrichment only. A lookup failure does not block the write; the record commits with available fields.
- Clarification is conversational: the system names the missing fields in plain language and waits for user response before re-checking.

## External Spec Lookup

Applies to new discrete/IC entries only (passives have no meaningful external record to look up).

**Provider priority**: LCSC primary, Digikey fallback.

**LCSC**: REST API, `part_number` as search key. No auth for basic product search. Returns: manufacturer, description, package (if absent), datasheet URL (discarded — not stored).

**Digikey**: REST API, requires OAuth2 client credentials. Credentials stored in a config file (`config.toml`) at startup — not hardcoded. Returns same field set as LCSC.

**Fallback logic**: try LCSC first; if no result or HTTP error, try Digikey. If both fail, commit without enrichment.

**Field mapping**: both APIs return `manufacturer` and `description` which map directly to the schema. `package` from the API is only used if the extracted record has `package = null`. Do not overwrite a user-provided package with an API-returned one.

**No caching**: lookup happens once at first ingest of a given part number. The result is stored in the record. Subsequent queries against the same part number hit the DB, not the API.

## Assumptions

- AliExpress labels are often ambiguous; photo + text is the normal ingestion path for non-obvious parts.
- Null fields signal incomplete extraction — no separate confidence score needed.
- Duplicate detection always increments; a second entry for the same part is always wrong.
- External lookup failure is non-fatal; record commits with available fields.
- LCSC does not require auth for basic part search; Digikey requires OAuth2 client credentials.

## Tradeoffs

- Null-as-ambiguity keeps the LLM↔completeness-check interface simple at the cost of no graded confidence signal. Acceptable given field completeness is already the confidence mechanism.
- Exact-match duplicate detection is simple but fragile to value normalization inconsistencies (e.g. "10k" vs "10kohm"). This is addressed by the shared normalization function defined in the Query unit — Ingestion applies the same normalization before duplicate matching.
