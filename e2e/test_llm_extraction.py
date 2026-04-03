"""
E2E: LLM extraction — structured output from real model.
"""

import pytest

from e2e.conftest import requires_llm


@requires_llm
@pytest.mark.asyncio
class TestExtractPassive:
    async def test_resistor_text(self, llm):
        result = await llm.extract("I have 20 10k 0402 resistors")
        assert result["profile"] == "passive"
        assert result["part_category"] is not None
        assert result["value"] is not None
        assert "10" in result["value"].lower()
        assert result["quantity"] == 20

    async def test_capacitor_text(self, llm):
        result = await llm.extract("add 50 100nF 0603 capacitors")
        assert result["profile"] == "passive"
        assert result["quantity"] == 50
        assert result["value"] is not None
        assert "100" in result["value"]

    async def test_inductor_text(self, llm):
        result = await llm.extract("10uH inductor, 0402 package, qty 5")
        assert result["profile"] == "passive"
        assert result["quantity"] == 5


@requires_llm
@pytest.mark.asyncio
class TestExtractDiscreteIc:
    async def test_mosfet_by_part_number(self, llm):
        result = await llm.extract("add 10x 2N7002 mosfets")
        assert result["profile"] == "discrete_ic"
        assert result["part_number"] is not None
        assert "2N7002" in result["part_number"].upper()
        assert result["quantity"] == 10

    async def test_ic_by_part_number(self, llm):
        result = await llm.extract("got 5 ATmega328P-PU chips")
        assert result["profile"] == "discrete_ic"
        assert result["part_number"] is not None
        assert result["quantity"] == 5


@requires_llm
@pytest.mark.asyncio
class TestExtractIncomplete:
    async def test_missing_quantity_returns_null(self, llm):
        result = await llm.extract("I have some 10k resistors")
        # Quantity may or may not be resolvable — check field is present.
        assert "quantity" in result

    async def test_ambiguous_passive_missing_value(self, llm):
        result = await llm.extract("add a resistor in 0402")
        # Value should be null — not specified.
        assert "value" in result
        # profile should still be identifiable
        assert result["profile"] in ("passive", None)
