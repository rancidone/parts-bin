"""Tests for spec lookup merge logic (no HTTP calls)."""

from ingestion.lookup import merge_specs


class TestMergeSpecs:
    def test_fills_manufacturer_and_description(self):
        record = {"package": "SOT-23", "manufacturer": None, "description": None}
        specs = {"manufacturer": "Nexperia", "description": "N-ch MOSFET"}
        merged = merge_specs(record, specs)
        assert merged["manufacturer"] == "Nexperia"
        assert merged["description"] == "N-ch MOSFET"

    def test_fills_package_when_null(self):
        record = {"package": None, "manufacturer": None, "description": None}
        specs = {"package": "SOT-23"}
        merged = merge_specs(record, specs)
        assert merged["package"] == "SOT-23"

    def test_does_not_overwrite_user_package(self):
        record = {"package": "SOT-323", "manufacturer": None, "description": None}
        specs = {"package": "SOT-23"}
        merged = merge_specs(record, specs)
        assert merged["package"] == "SOT-323"

    def test_empty_specs_leaves_record_unchanged(self):
        record = {"package": "0402", "manufacturer": None, "description": None}
        merged = merge_specs(record, {})
        assert merged == record

    def test_original_record_not_mutated(self):
        record = {"package": None, "manufacturer": None, "description": None}
        merge_specs(record, {"package": "SOT-23"})
        assert record["package"] is None
