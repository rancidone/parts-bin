CREATE TABLE IF NOT EXISTS parts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_category TEXT    NOT NULL,
    profile       TEXT    NOT NULL CHECK (profile IN ('passive', 'discrete_ic')),
    value         TEXT,              -- normalized canonical form; NULL for discrete_ic
    package       TEXT,              -- e.g. "0402", "SOT-23"; nullable
    part_number   TEXT,              -- manufacturer part number; NULL for passives
    quantity      INTEGER NOT NULL DEFAULT 0,
    manufacturer  TEXT,
    description   TEXT,
    created_at    TEXT    NOT NULL,  -- ISO 8601
    updated_at    TEXT    NOT NULL   -- ISO 8601
);

-- Passives: identity is (part_category, value, package)
CREATE UNIQUE INDEX IF NOT EXISTS uq_passive
    ON parts (part_category, value, package)
    WHERE part_number IS NULL;

-- Discretes/ICs: identity is manufacturer part number
CREATE UNIQUE INDEX IF NOT EXISTS uq_discrete
    ON parts (part_number)
    WHERE part_number IS NOT NULL;

CREATE TABLE IF NOT EXISTS part_field_provenance (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id              INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    field_name           TEXT    NOT NULL,
    field_value          TEXT,
    source_tier          TEXT    NOT NULL,
    source_kind          TEXT    NOT NULL,
    source_locator       TEXT,
    extraction_method    TEXT    NOT NULL,
    confidence_marker    TEXT,
    conflict_status      TEXT    NOT NULL DEFAULT 'clear',
    normalization_method TEXT,
    evidence             TEXT,
    competing_candidates TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_part_field_provenance
    ON part_field_provenance (part_id, field_name);

CREATE TABLE IF NOT EXISTS part_pending_field_review (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id        INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    field_name     TEXT    NOT NULL,
    proposed_value TEXT,
    provenance_json TEXT   NOT NULL,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_part_pending_field_review
    ON part_pending_field_review (part_id, field_name);
