"""Tests for query filter → attrs conversion and run_query pipeline."""

from unittest.mock import AsyncMock

import pytest

from db.persistence import init_db, upsert
from llm.client import ConversationHistory, LLMClient
from query.search import _filters_to_attrs, run_query

PASSIVE_RESISTOR = {
    "part_category": "resistor",
    "profile": "passive",
    "value": "10k",
    "package": "0402",
    "quantity": 10,
    "part_number": None,
    "manufacturer": None,
    "description": None,
}


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def llm():
    return AsyncMock(spec=LLMClient)


def _parsed(filters, freetext=None):
    return {"filters": filters, "freetext": freetext}


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


class TestRunQuery:
    async def test_returns_matching_parts(self, db, llm):
        upsert(db, PASSIVE_RESISTOR)
        llm.parse_query.return_value = _parsed([{"field": "part_category", "op": "eq", "value": "resistor"}])
        llm.answer.return_value = "You have 1 resistor."
        result = await run_query(db, llm, "do I have resistors?", ConversationHistory())
        assert result["type"] == "results"
        assert len(result["parts"]) == 1
        assert result["parts"][0]["part_category"] == "resistor"
        assert result["answer"] == "You have 1 resistor."

    async def test_returns_not_found_when_no_match(self, db, llm):
        llm.parse_query.return_value = _parsed([{"field": "part_category", "op": "eq", "value": "capacitor"}])
        llm.answer.return_value = "You don't have any capacitors."
        result = await run_query(db, llm, "do I have caps?", ConversationHistory())
        assert result["type"] == "not_found"
        assert "answer" in result

    async def test_llm_error_returns_error(self, db, llm):
        llm.parse_query.side_effect = ValueError("bad json")
        result = await run_query(db, llm, "anything?", ConversationHistory())
        assert result["type"] == "error"

    async def test_empty_filters_returns_all_parts(self, db, llm):
        upsert(db, PASSIVE_RESISTOR)
        upsert(db, {**PASSIVE_RESISTOR, "value": "22k"})
        llm.parse_query.return_value = _parsed([])
        llm.answer.return_value = "You have 2 resistors."
        result = await run_query(db, llm, "show me everything", ConversationHistory())
        assert result["type"] == "results"
        assert len(result["parts"]) == 2
