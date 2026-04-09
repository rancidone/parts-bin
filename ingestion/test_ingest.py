"""Tests for the run_ingestion pipeline."""

from unittest.mock import AsyncMock, patch

import pytest

from db.persistence import init_db, list_all
from ingestion.ingest import run_ingestion
from llm.client import LLMClient

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

INCOMPLETE_PASSIVE = {
    "part_category": "resistor",
    "profile": "passive",
    "value": None,  # missing
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


async def _collect(gen):
    events = []
    async for evt in gen:
        events.append(evt)
    return events


class TestRunIngestionPassive:
    async def test_complete_record_upserts_and_yields_result(self, db, llm):
        llm.extract.return_value = COMPLETE_PASSIVE
        events = await _collect(run_ingestion(db, llm, "add 10 10k 0402 resistors"))
        assert len(events) == 1
        assert events[0]["type"] == "result"
        assert events[0]["part"]["part_category"] == "resistor"
        assert len(list_all(db)) == 1

    async def test_incomplete_record_yields_clarification_not_upsert(self, db, llm):
        llm.extract.return_value = INCOMPLETE_PASSIVE
        events = await _collect(run_ingestion(db, llm, "add a resistor"))
        assert len(events) == 1
        assert events[0]["type"] == "clarification"
        assert len(list_all(db)) == 0

    async def test_clarification_mentions_missing_field(self, db, llm):
        llm.extract.return_value = INCOMPLETE_PASSIVE
        events = await _collect(run_ingestion(db, llm, "add a resistor"))
        assert "value" in events[0]["message"]

    async def test_llm_error_yields_error_not_upsert(self, db, llm):
        llm.extract.side_effect = ValueError("invalid json")
        events = await _collect(run_ingestion(db, llm, "add something"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert len(list_all(db)) == 0

    async def test_result_event_contains_assigned_id(self, db, llm):
        llm.extract.return_value = COMPLETE_PASSIVE
        events = await _collect(run_ingestion(db, llm, "add 10 resistors"))
        assert "id" in events[0]["part"]
        assert events[0]["part"]["id"] is not None


class TestRunIngestionDiscreteIc:
    async def test_fetches_specs_for_discrete_ic(self, db, llm):
        llm.extract.return_value = COMPLETE_DISCRETE
        mock_specs = {"manufacturer": "Vishay", "description": "N-ch MOSFET"}
        with patch("ingestion.ingest.fetch_specs", new=AsyncMock(return_value=mock_specs)) as mock_fetch:
            events = await _collect(run_ingestion(db, llm, "add 5 2N7002", digikey_credentials=None))
        mock_fetch.assert_called_once_with("2N7002", None)
        assert events[0]["type"] == "result"

    async def test_spec_fields_merged_into_result(self, db, llm):
        llm.extract.return_value = COMPLETE_DISCRETE
        mock_specs = {"manufacturer": "Vishay", "description": "N-ch MOSFET"}
        with patch("ingestion.ingest.fetch_specs", new=AsyncMock(return_value=mock_specs)):
            events = await _collect(run_ingestion(db, llm, "add 5 2N7002"))
        assert events[0]["part"]["manufacturer"] == "Vishay"

    async def test_insertion_succeeds_when_spec_lookup_returns_nothing(self, db, llm):
        llm.extract.return_value = COMPLETE_DISCRETE
        with patch("ingestion.ingest.fetch_specs", new=AsyncMock(return_value={})):
            events = await _collect(run_ingestion(db, llm, "add 5 2N7002"))
        assert events[0]["type"] == "result"
        assert events[0]["part"]["manufacturer"] is None
        assert len(list_all(db)) == 1

    async def test_skips_spec_lookup_when_no_part_number(self, db, llm):
        record = {**COMPLETE_DISCRETE, "part_number": None}
        # Without part_number, is_complete will fail — so give it a fake one and
        # clear it to test the lookup-skip branch separately via a passive record.
        # Instead, test that a discrete record with no part_number triggers clarification.
        llm.extract.return_value = record
        with patch("ingestion.ingest.fetch_specs", new=AsyncMock()) as mock_fetch:
            events = await _collect(run_ingestion(db, llm, "add a mosfet"))
        mock_fetch.assert_not_called()
        assert events[0]["type"] == "clarification"
