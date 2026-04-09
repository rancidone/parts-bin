"""
Persistence layer for the parts inventory.

All SQL lives here. Callers use upsert(), query(), list_all(), export_csv().
Normalization of `value` is applied here before every read and write.
"""

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

# EIA multiplier-as-decimal: letter signals both multiplier and decimal point.
# e.g. 2R2 -> 2.2r, 4u7 -> 4.7u, 5K1 -> 5.1k, 1M5 -> 1.5m
_EIA_RE = re.compile(
    r"^(\d+)(R|K|M|G|P|N|U|H)(\d+)$",
    re.IGNORECASE,
)

# Suffix normalization maps for each domain.
# Keys are what may appear after stripping unit labels (ohm/f/h/etc).
_RESISTANCE_SUFFIXES = {
    "r": "r", "ohm": "r", "": "r",       # base unit
    "k": "k", "kohm": "k",
    "m": "m", "mohm": "m",               # mega
    "g": "g",
}
_CAPACITANCE_SUFFIXES = {
    "p": "p", "pf": "p",
    "n": "n", "nf": "n",
    "u": "u", "uf": "u", "µ": "u", "µf": "u",
}
_INDUCTANCE_SUFFIXES = {
    "n": "n", "nh": "n",
    "u": "u", "uh": "u", "µ": "u", "µh": "u",
    "m": "m", "mh": "m",               # milli
}

# EIA letter → canonical suffix per domain
_EIA_LETTER_MAP = {
    "resistance": {"r": "r", "k": "k", "m": "m", "g": "g"},
    "capacitance": {"p": "p", "n": "n", "u": "u"},
    "inductance":  {"n": "n", "u": "u", "m": "m", "h": "m"},  # H alone = milli in inductance context
}

# Domain detection from part_category (lower-cased)
_CATEGORY_DOMAIN = {
    "resistor":  "resistance",
    "capacitor": "capacitance",
    "inductor":  "inductance",
}


def _domain_for_category(part_category: str) -> Optional[str]:
    return _CATEGORY_DOMAIN.get(part_category.lower())


def _expand_eia(raw: str, domain: str) -> Optional[str]:
    """
    Expand EIA multiplier-as-decimal notation into explicit decimal form.
    Returns expanded string (e.g. '2.2r') or None if not EIA format.
    """
    m = _EIA_RE.match(raw.strip())
    if not m:
        return None
    left, letter, right = m.group(1), m.group(2).lower(), m.group(3)
    letter_map = _EIA_LETTER_MAP.get(domain, {})
    canonical_suffix = letter_map.get(letter)
    if canonical_suffix is None:
        return None
    return f"{left}.{right}{canonical_suffix}"


def normalize_value(raw: str, part_category: str) -> str:
    """
    Normalize a raw value string to canonical form.

    Examples:
        normalize_value("10K",    "resistor")   -> "10k"
        normalize_value("2R2",    "resistor")   -> "2.2r"
        normalize_value("100nF",  "capacitor")  -> "100n"
        normalize_value("4u7",    "inductor")   -> "4.7u"
        normalize_value("0.1uF",  "capacitor")  -> "0.1u"
    """
    domain = _domain_for_category(part_category)
    if domain is None:
        # Unknown category — return lowercased as-is; no normalization possible.
        return raw.strip().lower()

    s = raw.strip().lower()

    # Try EIA multiplier-as-decimal first (before any other stripping).
    eia = _expand_eia(s, domain)
    if eia is not None:
        return eia

    # Strip trailing unit labels (ohm, f, h) before suffix lookup.
    for unit_label in ("ohm", "f", "h"):
        if s.endswith(unit_label):
            s = s[: -len(unit_label)]
            break

    # Split into numeric part and suffix.
    m = re.match(r"^([0-9]*\.?[0-9]+)([a-zµ]*)$", s)
    if not m:
        return raw.strip().lower()  # unrecognized format; pass through lowercased

    number, suffix = m.group(1), m.group(2)

    if domain == "resistance":
        canonical = _RESISTANCE_SUFFIXES.get(suffix)
        if canonical is None:
            canonical = "r"
    elif domain == "capacitance":
        canonical = _CAPACITANCE_SUFFIXES.get(suffix)
        if canonical is None:
            canonical = "u"
    elif domain == "inductance":
        canonical = _INDUCTANCE_SUFFIXES.get(suffix)
        if canonical is None:
            canonical = "u"
    else:
        canonical = suffix

    return f"{number}{canonical}"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> None:
    """Create schema if not already present."""
    schema = SCHEMA_PATH.read_text()
    conn = _connect(db_path)
    with conn:
        conn.executescript(schema)
    conn.close()


def _update_fields_with_conn(conn: sqlite3.Connection, part_id: int, fields: dict) -> int:
    allowed = {"part_category", "profile", "value", "package", "part_number",
               "manufacturer", "description"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return part_id
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    conn.execute(
        f"UPDATE parts SET {set_clause} WHERE id = :id",
        {**updates, "id": part_id},
    )
    return part_id


def _save_field_provenance_with_conn(
    conn: sqlite3.Connection,
    part_id: int,
    provenance_records: list[dict],
) -> None:
    now = _now()
    for record in provenance_records:
        conn.execute(
            """
            INSERT INTO part_field_provenance
                (part_id, field_name, field_value, source_tier, source_kind, source_locator,
                 extraction_method, confidence_marker, conflict_status, normalization_method,
                 competing_candidates, created_at, updated_at)
            VALUES
                (:part_id, :field_name, :field_value, :source_tier, :source_kind, :source_locator,
                 :extraction_method, :confidence_marker, :conflict_status, :normalization_method,
                 :competing_candidates, :created_at, :updated_at)
            ON CONFLICT(part_id, field_name) DO UPDATE SET
                field_value = excluded.field_value,
                source_tier = excluded.source_tier,
                source_kind = excluded.source_kind,
                source_locator = excluded.source_locator,
                extraction_method = excluded.extraction_method,
                confidence_marker = excluded.confidence_marker,
                conflict_status = excluded.conflict_status,
                normalization_method = excluded.normalization_method,
                competing_candidates = excluded.competing_candidates,
                updated_at = excluded.updated_at
            """,
            {
                "part_id": part_id,
                "field_name": record["field_name"],
                "field_value": record.get("field_value"),
                "source_tier": record["source_tier"],
                "source_kind": record["source_kind"],
                "source_locator": record.get("source_locator"),
                "extraction_method": record["extraction_method"],
                "confidence_marker": record.get("confidence_marker"),
                "conflict_status": record.get("conflict_status", "clear"),
                "normalization_method": record.get("normalization_method"),
                "competing_candidates": json.dumps(record.get("competing_candidates", [])),
                "created_at": now,
                "updated_at": now,
            },
        )


# ---------------------------------------------------------------------------
# Persistence layer public API
# ---------------------------------------------------------------------------

def upsert(db_path: str | Path, part: dict) -> int:
    """
    Insert a part or increment its quantity if it already exists.

    `part` keys:
        part_category (str, required)
        profile       (str, required: 'passive' | 'discrete_ic')
        quantity      (int, required)
        value         (str | None) — normalized by this function for passives
        package       (str | None)
        part_number   (str | None)
        manufacturer  (str | None)
        description   (str | None)

    Returns the id of the inserted or updated row.
    """
    p = dict(part)
    now = _now()

    # Normalize value for passives before touching the DB.
    if p.get("profile") == "passive" and p.get("value") is not None:
        p["value"] = normalize_value(p["value"], p["part_category"])

    conn = _connect(db_path)
    try:
        with conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO parts
                        (part_category, profile, value, package, part_number,
                         quantity, manufacturer, description, created_at, updated_at)
                    VALUES
                        (:part_category, :profile, :value, :package, :part_number,
                         :quantity, :manufacturer, :description, :created_at, :updated_at)
                    """,
                    {**p, "created_at": now, "updated_at": now},
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                # Duplicate — increment quantity.
                qty = p.get("quantity", 1)
                if p.get("profile") == "passive":
                    conn.execute(
                        """
                        UPDATE parts SET quantity = quantity + ?, updated_at = ?
                        WHERE part_category = ? AND value = ? AND package = ?
                          AND part_number IS NULL
                        """,
                        (qty, now, p["part_category"], p.get("value"), p.get("package")),
                    )
                    row = conn.execute(
                        "SELECT id FROM parts WHERE part_category = ? AND value = ? AND package = ? AND part_number IS NULL",
                        (p["part_category"], p.get("value"), p.get("package")),
                    ).fetchone()
                else:
                    conn.execute(
                        """
                        UPDATE parts SET quantity = quantity + ?, updated_at = ?
                        WHERE part_number = ?
                        """,
                        (qty, now, p["part_number"]),
                    )
                    row = conn.execute(
                        "SELECT id FROM parts WHERE part_number = ?",
                        (p["part_number"],),
                    ).fetchone()
                return row["id"]
    finally:
        conn.close()


def update_fields(db_path: str | Path, part_id: int, fields: dict) -> int:
    """
    Update non-null fields on an existing part without touching quantity.

    Only the fields listed in `fields` with non-None values are written.
    Returns part_id.
    """
    conn = _connect(db_path)
    try:
        with conn:
            _update_fields_with_conn(conn, part_id, fields)
    finally:
        conn.close()
    return part_id


def update_fields_with_provenance(
    db_path: str | Path,
    part_id: int,
    fields: dict,
    provenance_records: list[dict],
) -> int:
    """Update fields and durable provenance for the same part in one transaction."""
    conn = _connect(db_path)
    try:
        with conn:
            _update_fields_with_conn(conn, part_id, fields)
            _save_field_provenance_with_conn(conn, part_id, provenance_records)
    finally:
        conn.close()
    return part_id


def list_field_provenance(db_path: str | Path, part_id: int) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT field_name, field_value, source_tier, source_kind, source_locator,
                   extraction_method, confidence_marker, conflict_status,
                   normalization_method, competing_candidates
            FROM part_field_provenance
            WHERE part_id = ?
            ORDER BY field_name
            """,
            (part_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def query(db_path: str | Path, attrs: dict) -> list[dict]:
    """
    Query parts by structured attributes. NULL fields are wildcards.

    `attrs` keys (all optional):
        part_category, profile, value, package, part_number

    `value` is normalized before querying if part_category is provided.
    """
    a = dict(attrs)

    if a.get("value") is not None and a.get("part_category") is not None:
        a["value"] = normalize_value(a["value"], a["part_category"])

    conditions = []
    params = []
    for field in ("part_category", "profile", "value", "package", "part_number"):
        if a.get(field) is not None:
            conditions.append(f"{field} = ?")
            params.append(a[field])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM parts {where} ORDER BY part_category, value, part_number"

    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_by_id(db_path: str | Path, part_id: int) -> dict | None:
    """Return a single part by id, or None if not found."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_all(db_path: str | Path) -> list[dict]:
    """Return all parts ordered by category, then value/part_number."""
    return query(db_path, {})


def export_csv(rows: list[dict]) -> str:
    """
    Serialize a list of part dicts to CSV string.

    Column order matches UI-defined schema:
        part_category, value, package, quantity, part_number, manufacturer, description
    """
    fields = ["part_category", "value", "package", "quantity", "part_number", "manufacturer", "description"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()
