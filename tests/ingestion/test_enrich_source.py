import argparse
from unittest.mock import AsyncMock, patch

from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, GetPartRequest, PartFields, PartsBinService
from ingestion import enrich_source
from ingestion.supplied_source import POLICY_VERSION


async def test_operator_composes_sqlite_cache_and_stages_review(tmp_path):
    database = tmp_path / "parts.db"
    service = PartsBinService(SQLitePartsBinRepository(database))
    part = service.add_part(AddPartRequest(PartFields(
        part_category="transistor", profile="discrete_ic", quantity=7, part_number="PBSS5350T")))
    config = tmp_path / "config.toml"
    config.write_text(f'[db]\npath = "{database}"\n')
    url = "https://assets.nexperia.com/documents/data-sheet/PBSS5350T.pdf"
    candidate = {"outcome": "proposal", "fields": {
        "package": {"value": "SOT23", "evidence": {
            "page": 1, "excerpt": "in SOT23", "source_url": url}}},
        "source": {"url": url, "sha256": "hash", "retrieved_at": "2026-10-10"},
        "model": "explicit-model", "policy_version": POLICY_VERSION}
    args = argparse.Namespace(config=config, part_id=part.id, source_url=url,
                              model="explicit-model", refresh=True)
    with patch.object(enrich_source, "enrich", AsyncMock(return_value=candidate)) as enrich:
        result = await enrich_source.run(args)
    cache = enrich.call_args.kwargs["cache"]
    assert isinstance(cache, enrich_source.SQLiteEnrichmentCache)
    assert cache.path == database
    assert enrich.call_args.args == ("PBSS5350T", None, url)
    assert enrich.call_args.kwargs["refresh"] is True
    assert result["review_staged"] is True
    assert service.get(GetPartRequest(part.id)).package is None
    assert service.get(GetPartRequest(part.id)).quantity == 7
    assert part.id in service.list_pending_reviews()


async def test_operator_electrical_mode_uses_same_review_path(tmp_path):
    from tests.domain.test_datasheet import fact

    database = tmp_path / 'parts.db'
    service = PartsBinService(SQLitePartsBinRepository(database))
    part = service.add_part(AddPartRequest(PartFields('resistor', 'passive', 4, value='10k', part_number='EXACT-10K-F')))
    config = tmp_path / 'config.toml'
    config.write_text(f'[db]\npath = "{database}"\n')
    args = argparse.Namespace(config=config, part_id=part.id,
        source_url='https://www.vishay.com/docs/example.pdf', model='test', refresh=True, electrical=True)
    with patch('ingestion.datasheet.enrich', AsyncMock(return_value={'outcome': 'proposal', 'facts': [fact()]})) as enrich:
        result = await enrich_source.run(args)
    assert result['review_staged'] and result['facts'] == []
    assert result['pending_review']['facts'] == [fact()]
    assert enrich.call_args.kwargs['category'] == 'resistor'
    assert enrich.call_args.kwargs['refresh'] is True
    assert service.list_pending_reviews() == {}
    assert service.get(GetPartRequest(part.id)) == part
