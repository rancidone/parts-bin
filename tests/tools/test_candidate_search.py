"""Candidate discovery preserves committed identity and is read-only."""
import pytest

from db.repository import SQLitePartsBinRepository
from domain import (
    AddPartRequest, DomainError, PartFields, PartsBinService,
    SearchCandidatesRequest, SearchPartsRequest,
)
from tools import PartsBinToolRegistry


@pytest.fixture
def stock(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    def add(number, **fields):
        return service.add_part(AddPartRequest(PartFields(
            part_category='transistor', profile='discrete_ic', quantity=fields.pop('quantity', 4),
            part_number=number, **fields)))
    first = add('PBSS5350T,215', manufacturer='Nexperia', description='PNP transistor; marking 50T', package='SOT23')
    second = add('PBSS5350T', quantity=0, package='SOT23')
    third = add('OTHER-1', manufacturer='Other supplier', description='Low noise amplifier', package='DIP')
    service.repository.inventory.save_pending_review(third.id, {'description': 'pending 50T'}, [])
    return service, first, second, third


@pytest.mark.parametrize('query,indices', [
    ('pbss5350t', [1, 2]), ('  NEXPERIA  ', [1]), ('pnp', [1]),
    ('50t', [1, 2]), (',215', [1]), ('low noise', [3]),
    ('pending', []), ('%', []), ('_', []), ("' OR 1=1 --", []),
    ('PBSS5350T,216', []), ('unrecorded marking', []),
])
def test_literal_candidate_fields_and_identity(stock, query, indices):
    service, *parts = stock
    before = service.list()
    reviews = service.list_pending_reviews()
    result = service.search_candidates(SearchCandidatesRequest(query))
    assert result == [parts[index - 1] for index in indices]
    assert service.list() == before
    assert service.list_pending_reviews() == reviews
    assert service.search(SearchPartsRequest({'part_number': 'PBSS5350T'})) == [parts[1]]


def test_candidate_filters_and_stock_are_per_record(stock):
    service, first, _, _ = stock
    assert service.search_candidates(SearchCandidatesRequest('5350', minimum_quantity=1)) == [first]
    assert service.search_candidates(SearchCandidatesRequest('5350', minimum_quantity=5)) == []
    assert service.search_candidates(SearchCandidatesRequest('5350', {'package': 'DIP'})) == []
    assert service.search_candidates(SearchCandidatesRequest('5350', {'part_number': 'PBSS5350T,215'})) == [first]


@pytest.mark.parametrize('query', ['', '  ', None, 123, 'x' * 201])
def test_domain_rejects_invalid_candidate_query(stock, query):
    with pytest.raises(DomainError, match='Candidate query'):
        stock[0].search_candidates(SearchCandidatesRequest(query))


async def test_tool_labels_and_bounds_candidates(stock):
    service, first, second, _ = stock
    registry = PartsBinToolRegistry(service)
    result = await registry.execute('search_candidates', {'query': '5350', 'limit': 1})
    assert result['ok']
    assert result['result']['match_kind'] == 'candidate'
    assert result['result']['count'] == 2
    assert result['result']['truncated']
    assert result['result']['candidates'][0]['part_number'] == first.part_number
    assert 'created_at' not in result['result']['candidates'][0]
    full = await registry.execute('search_candidates', {'query': '5350'})
    assert [p['id'] for p in full['result']['candidates']] == [first.id, second.id]


@pytest.mark.parametrize('arguments', [
    {}, {'query': '  '}, {'query': None}, {'query': 'x' * 201},
    {'query': '5350', 'limit': 0}, {'query': '5350', 'limit': 101},
    {'query': '5350', 'minimum_quantity': True},
    {'query': '5350', 'filters': {'description': 'PNP'}},
    {'query': '5350', 'sql': 'SELECT * FROM parts'},
])
async def test_tool_rejects_invalid_search(stock, arguments):
    result = await PartsBinToolRegistry(stock[0]).execute('search_candidates', arguments)
    assert result['ok'] is False
    assert result['error']['code'] == 'invalid_input'
