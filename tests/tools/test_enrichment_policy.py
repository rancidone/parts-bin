"""Adding stock can deliberately avoid paid supplier retrieval."""

from unittest.mock import AsyncMock

import pytest

from db.repository import SQLitePartsBinRepository
from domain import PartsBinService
from tools import PartsBinToolRegistry, ToolExecutionContext


@pytest.fixture
def registry(tmp_path):
    service = PartsBinService(
        SQLitePartsBinRepository(tmp_path / "parts.db"),
        spec_fetcher=AsyncMock(return_value={"status": "matched", "chosen_updates": {}}),
    )
    return PartsBinToolRegistry(service)


@pytest.mark.parametrize("durable", [False, True])
async def test_thirty_type_assortment_skips_retrieval_and_replays_without_more_stock(registry, durable):
    for index in range(30):
        args = {
            "part_category": "integrated circuit", "profile": "discrete_ic",
            "part_number": f"KIT-IC-{index}", "quantity": 5,
            "description": "User-described kit contents", "enrich": False,
        }
        context = ToolExecutionContext(operation_id=f"kit-{index}") if durable else None
        added = await registry.execute("add_part", args, context=context)
        assert added["ok"], added
        assert added["result"]["enrichment"] == {"status": "skipped"}
        assert "enrich" not in added["result"]
        if durable:
            assert await registry.execute("add_part", args, context=context) == added

    registry.service.spec_fetcher.assert_not_awaited()
    parts = registry.service.list()
    assert len(parts) == 30
    assert all(part.quantity == 5 and part.description == "User-described kit contents" for part in parts)
    assert registry.service.list_pending_reviews() == {}

    # A deliberate later lookup remains available for one selected kit part.
    looked_up = await registry.execute("lookup_part_specs", {"part_id": parts[0].id})
    assert looked_up["ok"]
    registry.service.spec_fetcher.assert_awaited_once_with(parts[0].part_number)


@pytest.mark.parametrize("category,value", [("resistor", "10k"), ("capacitor", "100nF"), ("inductor", "10uH")])
async def test_passives_skip_automatic_lookup_even_with_supplier_code(registry, category, value):
    added = await registry.execute("add_part", {
        "part_category": category, "profile": "passive", "value": value,
        "part_number": "SUPPLIER-CODE", "quantity": 10, "enrich": True,
    })
    assert added["ok"], added
    registry.service.spec_fetcher.assert_not_awaited()


@pytest.mark.parametrize("option", [{}, {"enrich": True}])
@pytest.mark.parametrize("durable", [False, True])
async def test_regular_ic_keeps_automatic_enrichment(registry, option, durable):
    added = await registry.execute("add_part", {
        "part_category": "operational amplifier", "profile": "discrete_ic",
        "part_number": "NE5532", "quantity": 1, **option,
    }, context=ToolExecutionContext(operation_id="ic") if durable else None)
    assert added["ok"], added
    assert added["result"]["enrichment"]["status"] == "matched"
    registry.service.spec_fetcher.assert_awaited_once_with("NE5532")


@pytest.mark.parametrize("invalid", [None, "false", 0])
async def test_enrich_requires_boolean_before_adding_stock(registry, invalid):
    result = await registry.execute("add_part", {
        "part_category": "operational amplifier", "profile": "discrete_ic",
        "part_number": "NE5532", "quantity": 1, "enrich": invalid,
    })
    assert result["error"]["code"] == "invalid_input"
    assert registry.service.list() == []
    registry.service.spec_fetcher.assert_not_awaited()
