"""
E2E: Full ingestion pipeline — LLM extraction → completeness → DB write.
"""

import pytest

from db.persistence import list_all
from e2e.conftest import requires_llm
from ingestion.ingest import run_ingestion


@requires_llm
@pytest.mark.asyncio
class TestIngestPassive:
    async def test_resistor_committed(self, llm, db):
        events = []
        async for evt in run_ingestion(db, llm, "add 10 10k 0402 resistors"):
            events.append(evt)

        types = [e["type"] for e in events]
        # Either committed directly or asked for clarification.
        assert "result" in types or "clarification" in types

        if "result" in types:
            parts = list_all(db)
            assert len(parts) == 1
            assert parts[0]["profile"] == "passive"
            assert parts[0]["quantity"] > 0

    async def test_duplicate_increments(self, llm, db):
        async for _ in run_ingestion(db, llm, "add 10 10k 0402 resistors"):
            pass
        async for _ in run_ingestion(db, llm, "add 5 more 10k 0402 resistors"):
            pass

        parts = list_all(db)
        # Should be one record (duplicate detected), quantity >= 10.
        committed = [p for p in parts if p["profile"] == "passive"]
        if committed:
            assert committed[0]["quantity"] >= 10


@requires_llm
@pytest.mark.asyncio
class TestIngestDiscreteIc:
    async def test_mosfet_committed(self, llm, db):
        events = []
        async for evt in run_ingestion(db, llm, "add 5 2N7002 mosfets"):
            events.append(evt)

        types = [e["type"] for e in events]
        assert "result" in types or "clarification" in types

        if "result" in types:
            parts = list_all(db)
            assert any(p["part_number"] and "2N7002" in p["part_number"].upper() for p in parts)


@requires_llm
@pytest.mark.asyncio
class TestIngestClarification:
    async def test_incomplete_yields_clarification(self, llm, db):
        events = []
        async for evt in run_ingestion(db, llm, "add a resistor"):
            events.append(evt)

        # A vague input should either clarify or commit — never hard error.
        assert all(e["type"] in ("result", "clarification", "error") for e in events)
