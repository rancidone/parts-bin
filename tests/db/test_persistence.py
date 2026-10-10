"""
Tests for SQLite inventory persistence.
"""

import sqlite3

import pytest

from db.persistence import (
    init_db,
    list_all,
    list_field_provenance,
    query,
    update_fields_with_provenance,
    insert_part,
)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


PASSIVE_RESISTOR = {
    "part_category": "resistor",
    "profile": "passive",
    "value": "10k",  # adapter fixtures already use domain-normalized fields
    "package": "0402",
    "part_number": None,
    "quantity": 10,
    "manufacturer": None,
    "description": None,
}

DISCRETE_IC = {
    "part_category": "mosfet",
    "profile": "discrete_ic",
    "value": None,
    "package": "SOT-23",
    "part_number": "2N7002",
    "quantity": 5,
    "manufacturer": "Nexperia",
    "description": "N-channel MOSFET",
}


class TestInsertPassive:
    def test_insert(self, db):
        row_id = insert_part(db, PASSIVE_RESISTOR)
        assert row_id is not None
        rows = list_all(db)
        assert len(rows) == 1
        assert rows[0]["value"] == "10k"  # normalized

    def test_different_value_is_new_row(self, db):
        insert_part(db, PASSIVE_RESISTOR)
        insert_part(db, {**PASSIVE_RESISTOR, "value": "22k"})
        assert len(list_all(db)) == 2


class TestInsertDiscreteIc:
    def test_insert(self, db):
        insert_part(db, DISCRETE_IC)
        rows = list_all(db)
        assert len(rows) == 1
        assert rows[0]["part_number"] == "2N7002"


class TestQuery:
    def test_exact_match(self, db):
        insert_part(db, PASSIVE_RESISTOR)
        results = query(db, {"part_category": "resistor", "value": "10k"})
        assert len(results) == 1

    def test_null_field_is_wildcard(self, db):
        insert_part(db, PASSIVE_RESISTOR)
        insert_part(db, {**PASSIVE_RESISTOR, "package": "0603", "value": "10k"})
        results = query(db, {"part_category": "resistor", "value": "10k"})
        assert len(results) == 2

    def test_no_match(self, db):
        insert_part(db, PASSIVE_RESISTOR)
        results = query(db, {"part_category": "resistor", "value": "47k"})
        assert results == []


class TestFieldProvenance:
    def test_update_fields_with_provenance_persists_records(self, db):
        part_id = insert_part(db, DISCRETE_IC)

        update_fields_with_provenance(
            db,
            part_id,
            {"manufacturer": "Texas Instruments"},
            [{
                "field_name": "manufacturer",
                "field_value": "Texas Instruments",
                "source_tier": "primary_api",
                "source_kind": "api",
                "source_locator": "https://example.com/product",
                "extraction_method": "api",
                "confidence_marker": "high",
                "conflict_status": "clear",
                "normalization_method": "direct_copy",
                "evidence": "Manufacturer: Texas Instruments",
                "competing_candidates": [],
            }],
        )

        provenance = list_field_provenance(db, part_id)
        assert len(provenance) == 1
        assert provenance[0]["field_name"] == "manufacturer"
        assert provenance[0]["field_value"] == "Texas Instruments"
        assert provenance[0]["evidence"] == "Manufacturer: Texas Instruments"

    def test_update_fields_with_provenance_replaces_stale_field_record(self, db):
        part_id = insert_part(db, DISCRETE_IC)

        update_fields_with_provenance(
            db,
            part_id,
            {"manufacturer": "Texas Instruments"},
            [{
                "field_name": "manufacturer",
                "field_value": "Texas Instruments",
                "source_tier": "primary_api",
                "source_kind": "api",
                "source_locator": "https://example.com/first",
                "extraction_method": "api",
                "confidence_marker": "high",
                "conflict_status": "clear",
                "normalization_method": "direct_copy",
                "evidence": "Manufacturer: Texas Instruments",
                "competing_candidates": [],
            }],
        )
        update_fields_with_provenance(
            db,
            part_id,
            {"manufacturer": "TI"},
            [{
                "field_name": "manufacturer",
                "field_value": "TI",
                "source_tier": "primary_api",
                "source_kind": "api",
                "source_locator": "https://example.com/second",
                "extraction_method": "api",
                "confidence_marker": "high",
                "conflict_status": "clear",
                "normalization_method": "direct_copy",
                "evidence": "Manufacturer: TI",
                "competing_candidates": [],
            }],
        )

        provenance = list_field_provenance(db, part_id)
        assert len(provenance) == 1
        assert provenance[0]["field_value"] == "TI"
        assert provenance[0]["source_locator"] == "https://example.com/second"
        assert provenance[0]["evidence"] == "Manufacturer: TI"

    def test_init_db_adds_missing_evidence_column_for_existing_provenance_table(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE part_field_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                field_value TEXT,
                source_tier TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_locator TEXT,
                extraction_method TEXT NOT NULL,
                confidence_marker TEXT,
                conflict_status TEXT NOT NULL DEFAULT 'clear',
                normalization_method TEXT,
                competing_candidates TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

        init_db(db_path)

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(part_field_provenance)").fetchall()}
        conn.close()

        assert "evidence" in columns
