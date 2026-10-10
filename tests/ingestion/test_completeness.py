"""Tests for completeness check."""

from ingestion.completeness import clarification_prompt, is_complete, missing_fields

COMPLETE_PASSIVE = {
    "part_category": "resistor",
    "profile": "passive",
    "value": "10k",
    "package": "0402",
    "quantity": 10,
    "part_number": None,
    "manufacturer": None,
    "description": None,
}

COMPLETE_DISCRETE = {
    "part_category": "mosfet",
    "profile": "discrete_ic",
    "value": None,
    "package": "SOT-23",
    "part_number": "2N7002",
    "quantity": 5,
    "manufacturer": None,
    "description": None,
}


class TestMissingFields:
    def test_complete_passive(self):
        assert missing_fields(COMPLETE_PASSIVE) == []

    def test_complete_discrete(self):
        assert missing_fields(COMPLETE_DISCRETE) == []

    def test_missing_profile(self):
        record = {**COMPLETE_PASSIVE, "profile": None}
        assert "profile" in missing_fields(record)

    def test_passive_missing_value(self):
        record = {**COMPLETE_PASSIVE, "value": None}
        assert "value" in missing_fields(record)

    def test_passive_missing_package(self):
        record = {**COMPLETE_PASSIVE, "package": None}
        assert "package" in missing_fields(record)

    def test_discrete_missing_part_number(self):
        record = {**COMPLETE_DISCRETE, "part_number": None}
        assert "part_number" in missing_fields(record)

    def test_missing_part_category(self):
        record = {**COMPLETE_PASSIVE, "part_category": None}
        assert "part_category" in missing_fields(record)


class TestIsComplete:
    def test_complete_passive(self):
        assert is_complete(COMPLETE_PASSIVE)

    def test_complete_discrete(self):
        assert is_complete(COMPLETE_DISCRETE)

    def test_incomplete_passive(self):
        assert not is_complete({**COMPLETE_PASSIVE, "value": None})


class TestClarificationPrompt:
    def test_mentions_missing_field(self):
        record = {**COMPLETE_PASSIVE, "value": None}
        prompt = clarification_prompt(record)
        assert "value" in prompt

    def test_mentions_multiple_missing(self):
        record = {**COMPLETE_PASSIVE, "value": None, "package": None}
        prompt = clarification_prompt(record)
        assert "value" in prompt
        assert "package" in prompt
