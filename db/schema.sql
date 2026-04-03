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
