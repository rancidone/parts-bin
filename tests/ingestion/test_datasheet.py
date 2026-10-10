from unittest.mock import AsyncMock, patch

import httpx
import pytest

from domain import DomainError, PartFields
from domain.models import Part
from ingestion.datasheet import DatasheetFetcher


@pytest.mark.parametrize('failure', [TimeoutError(), httpx.ConnectError('secret body'), ValueError('secret body')])
async def test_retrieval_failure_is_unavailable_not_no_match(failure):
    fetcher = DatasheetFetcher(api_key='secret', model='test', cache=None)
    part = Part(id=1, created_at='now', updated_at='now',
                **vars(PartFields('resistor', 'passive', 4, value='10k', part_number='EXACT-10K-F')))
    with patch('ingestion.datasheet.enrich', AsyncMock(side_effect=failure)):
        with pytest.raises(DomainError) as error:
            await fetcher(part, 'https://www.vishay.com/docs/example.pdf')
    assert error.value.code == 'enrichment_unavailable'
    assert 'secret' not in error.value.message


async def test_invalid_url_is_rejected_before_download():
    fetcher = DatasheetFetcher(api_key='fake', model='test', cache=None)
    with patch('ingestion.datasheet.enrich', AsyncMock()) as enrich:
        with pytest.raises(DomainError) as error:
            await fetcher(None, 'https://127.0.0.1/internal')
    assert error.value.code == 'invalid_input'
    enrich.assert_not_awaited()


@pytest.mark.parametrize('saved_url', [None, 'https://www.vishay.com/docs/example.pdf', 'https://www.vishay.com/docs/other.pdf'])
async def test_saved_link_implicitly_asserts_datasheet_association(saved_url):
    url = 'https://www.vishay.com/docs/example.pdf'
    fetcher = DatasheetFetcher(api_key='fake', model='test', cache=None)
    part = Part(id=1, created_at='now', updated_at='now',
                **vars(PartFields('resistor', 'passive', 4, part_number='EXACT', datasheet_url=saved_url)))
    with patch('ingestion.datasheet.enrich', AsyncMock(return_value={'outcome': 'proposal', 'facts': []})) as enrich:
        await fetcher(part, url)
    assert enrich.await_args.kwargs['linked_part'] is (saved_url == url)
