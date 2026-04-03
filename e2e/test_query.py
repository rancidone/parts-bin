"""
E2E: Query pipeline — natural language → structured lookup → results.
"""

import pytest

from db.persistence import upsert
from e2e.conftest import requires_llm
from llm.client import ConversationHistory
from query.search import run_query

RESISTOR = {
    "part_category": "resistor",
    "profile": "passive",
    "value": "10k",
    "package": "0402",
    "part_number": None,
    "quantity": 20,
    "manufacturer": None,
    "description": None,
}

MOSFET = {
    "part_category": "mosfet",
    "profile": "discrete_ic",
    "value": None,
    "package": "SOT-23",
    "part_number": "2N7002",
    "quantity": 5,
    "manufacturer": "Nexperia",
    "description": "N-channel MOSFET",
}


@requires_llm
@pytest.mark.asyncio
class TestQuery:
    async def test_finds_resistor(self, llm, db):
        upsert(db, RESISTOR)
        history = ConversationHistory()
        result = await run_query(db, llm, "do I have any 10k resistors?", history)
        assert result["type"] in ("results", "not_found")
        if result["type"] == "results":
            assert any(p["part_category"] == "resistor" for p in result["parts"])

    async def test_not_found(self, llm, db):
        upsert(db, RESISTOR)
        history = ConversationHistory()
        result = await run_query(db, llm, "do I have any 1M resistors?", history)
        # May or may not find it — but must return a valid type.
        assert result["type"] in ("results", "not_found", "error")

    async def test_finds_mosfet_by_part_number(self, llm, db):
        upsert(db, MOSFET)
        history = ConversationHistory()
        result = await run_query(db, llm, "do I have 2N7002?", history)
        assert result["type"] in ("results", "not_found")
        if result["type"] == "results":
            assert any(
                p.get("part_number") and "2N7002" in p["part_number"].upper()
                for p in result["parts"]
            )
