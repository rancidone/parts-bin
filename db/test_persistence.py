"""
Tests for normalization and persistence layer.
"""

import tempfile
from pathlib import Path

import pytest

from db.persistence import (
    export_csv,
    init_db,
    list_all,
    list_field_provenance,
    normalize_value,
    query,
    update_fields_with_provenance,
    upsert,
)


# ---------------------------------------------------------------------------
# normalize_value
# ---------------------------------------------------------------------------

class TestNormalizeResistance:
    def test_bare_r(self):
        assert normalize_value("10R", "resistor") == "10r"

    def test_ohm_suffix(self):
        assert normalize_value("10ohm", "resistor") == "10r"

    def test_k(self):
        assert normalize_value("10K", "resistor") == "10k"

    def test_kohm(self):
        assert normalize_value("10kohm", "resistor") == "10k"

    def test_mega(self):
        assert normalize_value("1M", "resistor") == "1m"

    def test_eia_2r2(self):
        assert normalize_value("2R2", "resistor") == "2.2r"

    def test_eia_5k1(self):
        assert normalize_value("5K1", "resistor") == "5.1k"

    def test_eia_1m5(self):
        assert normalize_value("1M5", "resistor") == "1.5m"

    def test_decimal(self):
        assert normalize_value("2.2", "resistor") == "2.2r"

    def test_case_insensitive(self):
        assert normalize_value("10k", "resistor") == normalize_value("10K", "resistor")


class TestNormalizeCapacitance:
    def test_nf(self):
        assert normalize_value("100nF", "capacitor") == "100n"

    def test_uf_to_u(self):
        assert normalize_value("0.1uF", "capacitor") == "0.1u"

    def test_pf(self):
        assert normalize_value("100pF", "capacitor") == "100p"

    def test_eia_2n2(self):
        assert normalize_value("2n2", "capacitor") == "2.2n"

    def test_eia_4u7(self):
        assert normalize_value("4u7", "capacitor") == "4.7u"


class TestNormalizeInductance:
    def test_uh(self):
        assert normalize_value("10uH", "inductor") == "10u"

    def test_mh(self):
        assert normalize_value("0.01mH", "inductor") == "0.01m"

    def test_eia_4u7(self):
        assert normalize_value("4u7", "inductor") == "4.7u"


class TestNormalizeUnknownCategory:
    def test_passthrough_lowercased(self):
        assert normalize_value("ABC123", "transistor") == "abc123"


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
    "value": "10K",  # intentionally un-normalized — persistence layer must normalize
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


class TestUpsertPassive:
    def test_insert(self, db):
        row_id = upsert(db, PASSIVE_RESISTOR)
        assert row_id is not None
        rows = list_all(db)
        assert len(rows) == 1
        assert rows[0]["value"] == "10k"  # normalized

    def test_duplicate_increments_quantity(self, db):
        upsert(db, PASSIVE_RESISTOR)
        upsert(db, {**PASSIVE_RESISTOR, "quantity": 5})
        rows = list_all(db)
        assert len(rows) == 1
        assert rows[0]["quantity"] == 15

    def test_different_value_is_new_row(self, db):
        upsert(db, PASSIVE_RESISTOR)
        upsert(db, {**PASSIVE_RESISTOR, "value": "22k"})
        assert len(list_all(db)) == 2


class TestUpsertDiscreteIc:
    def test_insert(self, db):
        upsert(db, DISCRETE_IC)
        rows = list_all(db)
        assert len(rows) == 1
        assert rows[0]["part_number"] == "2N7002"

    def test_duplicate_increments_quantity(self, db):
        upsert(db, DISCRETE_IC)
        upsert(db, {**DISCRETE_IC, "quantity": 3})
        rows = list_all(db)
        assert len(rows) == 1
        assert rows[0]["quantity"] == 8


class TestQuery:
    def test_exact_match(self, db):
        upsert(db, PASSIVE_RESISTOR)
        results = query(db, {"part_category": "resistor", "value": "10K"})
        assert len(results) == 1

    def test_null_field_is_wildcard(self, db):
        upsert(db, PASSIVE_RESISTOR)
        upsert(db, {**PASSIVE_RESISTOR, "package": "0603", "value": "10K"})
        results = query(db, {"part_category": "resistor", "value": "10K"})
        assert len(results) == 2

    def test_no_match(self, db):
        upsert(db, PASSIVE_RESISTOR)
        results = query(db, {"part_category": "resistor", "value": "47k"})
        assert results == []


class TestExportCsv:
    def test_column_order(self, db):
        upsert(db, PASSIVE_RESISTOR)
        rows = list_all(db)
        csv_str = export_csv(rows)
        header = csv_str.splitlines()[0]
        assert header == "part_category,value,package,quantity,part_number,manufacturer,description"

    def test_row_content(self, db):
        upsert(db, PASSIVE_RESISTOR)
        rows = list_all(db)
        csv_str = export_csv(rows)
        lines = csv_str.splitlines()
        assert "resistor" in lines[1]
        assert "10k" in lines[1]


class TestFieldProvenance:
    def test_update_fields_with_provenance_persists_records(self, db):
        part_id = upsert(db, DISCRETE_IC)

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
                "competing_candidates": [],
            }],
        )

        provenance = list_field_provenance(db, part_id)
        assert len(provenance) == 1
        assert provenance[0]["field_name"] == "manufacturer"
        assert provenance[0]["field_value"] == "Texas Instruments"

    def test_update_fields_with_provenance_replaces_stale_field_record(self, db):
        part_id = upsert(db, DISCRETE_IC)

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
                "competing_candidates": [],
            }],
        )

        provenance = list_field_provenance(db, part_id)
        assert len(provenance) == 1
        assert provenance[0]["field_value"] == "TI"
        assert provenance[0]["source_locator"] == "https://example.com/second"
