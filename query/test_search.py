"""Tests for query filter → attrs conversion (pure logic)."""

from query.search import _filters_to_attrs


class TestFiltersToAttrs:
    def test_eq_filter(self):
        filters = [{"field": "part_category", "op": "eq", "value": "resistor"}]
        attrs = _filters_to_attrs(filters, None)
        assert attrs["part_category"] == "resistor"

    def test_equals_sign_op(self):
        filters = [{"field": "part_category", "op": "=", "value": "capacitor"}]
        attrs = _filters_to_attrs(filters, None)
        assert attrs["part_category"] == "capacitor"

    def test_unknown_field_ignored(self):
        filters = [{"field": "datasheet", "op": "eq", "value": "something"}]
        attrs = _filters_to_attrs(filters, None)
        assert "datasheet" not in attrs

    def test_non_eq_op_ignored(self):
        filters = [{"field": "quantity", "op": "gt", "value": "5"}]
        attrs = _filters_to_attrs(filters, None)
        assert "quantity" not in attrs

    def test_value_normalized_with_category(self):
        filters = [
            {"field": "part_category", "op": "eq", "value": "resistor"},
            {"field": "value",         "op": "eq", "value": "10K"},
        ]
        attrs = _filters_to_attrs(filters, None)
        assert attrs["value"] == "10k"

    def test_multiple_filters(self):
        filters = [
            {"field": "part_category", "op": "eq", "value": "capacitor"},
            {"field": "package",       "op": "eq", "value": "0402"},
        ]
        attrs = _filters_to_attrs(filters, None)
        assert attrs["part_category"] == "capacitor"
        assert attrs["package"] == "0402"
