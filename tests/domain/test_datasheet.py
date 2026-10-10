from unittest.mock import AsyncMock

import pytest

from db.repository import SQLitePartsBinRepository
from domain import (AddPartRequest, AddStockRequest, DomainError, GetPartRequest,
                    IngestDatasheetRequest, PartFields, PartsBinService, UpdatePartRequest)
from tools import PartsBinToolRegistry


def fact(value='0.25 W'):
    return {'name': 'rated_power', 'value': value, 'basis': 'rated',
            'conditions': {'ambient_temperature': '70 °C'}, 'evidence': {
                'kind': 'source', 'url': 'https://www.vishay.com/docs/example.pdf',
                'page': 1, 'sha256': 'a' * 64, 'retrieved_at': '2026-10-10T00:00:00+00:00',
                'part_number': 'EXACT-10K-F', 'excerpt': 'EXACT-10K-F: 0.25 W at 70 °C'}}


@pytest.fixture
def setup(tmp_path):
    fetcher = AsyncMock(return_value={'outcome': 'proposal', 'facts': [fact()]})
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'), datasheet_fetcher=fetcher)
    part = service.add_part(AddPartRequest(PartFields('resistor', 'passive', 4, value='10k', part_number='EXACT-10K-F')))
    return service, part, fetcher


async def test_pending_review_blocks_paid_retrieval(setup):
    service, part, fetcher = setup
    service.stage_specifications(part, [fact()])
    with pytest.raises(DomainError, match='Resolve the pending'):
        await service.ingest_datasheet(IngestDatasheetRequest(part.id, 'https://www.vishay.com/docs/example.pdf'))
    fetcher.assert_not_awaited()


@pytest.mark.parametrize('change', ['identity', 'accepted', 'pending', 'quantity', 'nominal'])
async def test_retrieval_concurrency_preserves_committed_data(setup, change):
    service, part, fetcher = setup
    async def retrieve(*_):
        if change == 'identity':
            service.update_part(UpdatePartRequest(part.id, {'package': 'user-package'}))
        elif change == 'accepted':
            service.stage_specifications(part, [fact('0.125 W')])
            service.apply_specification_review(part.id)
        elif change == 'pending':
            service.stage_specifications(part, [fact('0.125 W')])
        elif change == 'quantity':
            service.add_stock(AddStockRequest(part.id, 2))
        facts = [fact()] if change != 'nominal' else [{**fact(), 'name': 'resistance', 'value': '22 kΩ', 'basis': 'nominal'}]
        return {'outcome': 'proposal', 'facts': facts}
    fetcher.side_effect = retrieve
    request = IngestDatasheetRequest(part.id, 'https://www.vishay.com/docs/example.pdf')
    if change == 'quantity':
        result = await service.ingest_datasheet(request)
        assert result['review_staged']
        service.apply_specification_review(part.id)
        assert service.get(GetPartRequest(part.id)).quantity == 6
    else:
        with pytest.raises(DomainError) as error:
            await service.ingest_datasheet(request)
        assert error.value.code == 'conflict'
        state = service.get_specifications(part.id)
        if change == 'accepted':
            assert state['facts'] == [fact('0.125 W')] and state['pending_review'] is None
        elif change == 'pending':
            assert state['pending_review']['facts'] == [fact('0.125 W')]
        else:
            assert state['pending_review'] is None


@pytest.mark.parametrize('outcome', ['no_match', 'needs_clarification', 'proposal'])
async def test_empty_results_stage_nothing(setup, outcome):
    service, part, fetcher = setup
    fetcher.return_value = {'outcome': outcome, 'facts': [], 'clarification': 'Supply an exact source'}
    result = await service.ingest_datasheet(IngestDatasheetRequest(part.id, 'https://www.vishay.com/docs/example.pdf'))
    assert result['outcome'] == outcome and not result['review_staged']
    assert service.get(GetPartRequest(part.id)) == part


async def test_unmarked_stock_and_unsupported_category_do_not_retrieve(setup):
    service, part, fetcher = setup
    service.update_part(UpdatePartRequest(part.id, {'part_number': None}))
    with pytest.raises(DomainError, match='ordering code'):
        await service.ingest_datasheet(IngestDatasheetRequest(part.id, 'https://www.vishay.com/a.pdf'))
    service.update_part(UpdatePartRequest(part.id, {'part_category': 'unmarked', 'part_number': 'EXACT-10K-F'}))
    with pytest.raises(DomainError, match='category'):
        await service.ingest_datasheet(IngestDatasheetRequest(part.id, 'https://www.vishay.com/a.pdf'))
    fetcher.assert_not_awaited()


async def test_tool_contract_accepts_only_target_and_url(setup):
    service, part, fetcher = setup
    registry = PartsBinToolRegistry(service)
    for extra in ['facts', 'quantity', 'model', 'api_key', 'refresh']:
        result = await registry.execute('ingest_datasheet', {'part_id': part.id, 'source_url': 'https://www.vishay.com/a.pdf', extra: []})
        assert result['error']['code'] == 'invalid_input'
    fetcher.assert_not_awaited()


async def test_unconfigured_extraction_has_stable_tool_error(setup):
    service, part, _ = setup
    registry = PartsBinToolRegistry(PartsBinService(service.repository))
    result = await registry.execute('ingest_datasheet', {'part_id': part.id, 'source_url': 'https://www.vishay.com/a.pdf'})
    assert result['error']['code'] == 'enrichment_unavailable'
