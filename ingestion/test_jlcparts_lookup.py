"""Tests for jlcparts local database lookup."""

import sqlite3
import pytest

from ingestion.jlcparts_lookup import lookup_by_mpn


def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "test_jlcparts.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE manufacturers (
            id INTEGER PRIMARY KEY NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL
        );
        CREATE TABLE components (
            lcsc INTEGER PRIMARY KEY NOT NULL,
            category_id INTEGER NOT NULL,
            mfr TEXT NOT NULL,
            package TEXT NOT NULL,
            joints INTEGER NOT NULL,
            manufacturer_id INTEGER NOT NULL,
            basic INTEGER NOT NULL,
            preferred INTEGER NOT NULL DEFAULT 0,
            description TEXT NOT NULL,
            datasheet TEXT NOT NULL,
            stock INTEGER NOT NULL,
            price TEXT NOT NULL,
            last_update INTEGER NOT NULL,
            extra TEXT,
            last_on_stock INTEGER NOT NULL DEFAULT 0,
            flag INTEGER NOT NULL DEFAULT 0
        );
        CREATE VIEW v_components AS
            SELECT
                c.lcsc AS lcsc,
                c.category_id AS category_id,
                cat.category AS category,
                cat.subcategory AS subcategory,
                c.mfr AS mfr,
                c.package AS package,
                c.joints AS joints,
                m.name AS manufacturer,
                c.basic AS basic,
                c.preferred AS preferred,
                c.description AS description,
                c.datasheet AS datasheet,
                c.stock AS stock,
                c.last_on_stock AS last_on_stock,
                c.price AS price,
                c.extra AS extra
            FROM components c
            LEFT JOIN manufacturers m ON c.manufacturer_id = m.id
            LEFT JOIN categories cat ON c.category_id = cat.id;

        INSERT INTO manufacturers VALUES (1, 'Texas Instruments');
        INSERT INTO categories VALUES (1, 'Integrated Circuits', 'DC-DC Converters');
        INSERT INTO components VALUES
            (7063, 1, 'TLV62565DBVR', 'SOT-23-5', 5, 1, 1, 0,
             'Buck Switching Regulator IC Positive Adjustable 0.6V 1A SOT-23-5',
             'https://www.ti.com/lit/ds/symlink/tlv62565.pdf',
             1000, '[]', 1700000000, NULL, 1700000000, 0);
    """)
    conn.commit()
    conn.close()
    return db_path


class TestLookupByMpn:
    def test_exact_match_returns_ok(self, tmp_path):
        db = _make_db(tmp_path)
        result = lookup_by_mpn(db, "TLV62565DBVR")
        assert result["status"] == "ok"
        assert result["specs"]["part_number"] == "TLV62565DBVR"
        assert result["specs"]["manufacturer"] == "Texas Instruments"
        assert result["specs"]["package"] == "SOT-23-5"
        assert "Buck Switching Regulator" in result["specs"]["description"]

    def test_case_insensitive_match(self, tmp_path):
        db = _make_db(tmp_path)
        result = lookup_by_mpn(db, "tlv62565dbvr")
        assert result["status"] == "ok"
        assert result["specs"]["part_number"] == "TLV62565DBVR"

    def test_no_match_returns_no_match(self, tmp_path):
        db = _make_db(tmp_path)
        result = lookup_by_mpn(db, "NONEXISTENT123")
        assert result["status"] == "no-match"
        assert result["specs"] is None
        assert result["debug"] is None

    def test_debug_includes_lcsc_number(self, tmp_path):
        db = _make_db(tmp_path)
        result = lookup_by_mpn(db, "TLV62565DBVR")
        assert result["debug"]["lcsc_number"] == "C7063"
        assert result["debug"]["requested_part_number"] == "TLV62565DBVR"

    def test_debug_includes_datasheet_url(self, tmp_path):
        db = _make_db(tmp_path)
        result = lookup_by_mpn(db, "TLV62565DBVR")
        assert result["debug"]["datasheet_url"] == "https://www.ti.com/lit/ds/symlink/tlv62565.pdf"

    def test_missing_db_returns_failed(self, tmp_path):
        result = lookup_by_mpn(str(tmp_path / "nonexistent.sqlite3"), "TLV62565DBVR")
        assert result["status"] == "failed"
        assert result["error"] is not None
