"""Evaluate complete, bounded lookup through the canonical agent tool contract."""
import pytest

from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, DomainError, PartFields, PartsBinService, SearchPartsRequest
from tools import PartsBinToolRegistry


@pytest.fixture
def service(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    # Reverse ordering codes ensure pages follow IDs rather than lexical fields;
    # nominal identity and quantities remain separate for distinct records.
    for i in range(7):
        service.add_part(AddPartRequest(PartFields(
            'resistor', 'passive', i, value='10k', package=f'PKG-{i}',
            part_number=f'EXACT-{7 - i},215', description='candidate resistor')))
    return service


@pytest.mark.parametrize('tool,arguments,key', [
    ('search_parts', {'filters': {'value': '10000 ohms'}, 'minimum_quantity': 1}, 'parts'),
    ('search_candidates', {'query': 'candidate', 'minimum_quantity': 1}, 'candidates'),
])
async def test_all_pages_are_complete_stable_and_bounded(service, tool, arguments, key):
    registry = PartsBinToolRegistry(service)
    before = service.list()
    expected = [p.id for p in sorted(before, key=lambda p: p.id) if p.quantity >= 1]
    ids, offset = [], 0
    while True:
        response = await registry.execute(tool, {**arguments, 'limit': 2, 'offset': offset})
        assert response['ok']
        page = response['result']
        assert page['count'] == len(expected)
        assert len(page[key]) <= 2
        assert all(p['part_number'].endswith(',215') for p in page[key])
        ids.extend(p['id'] for p in page[key])
        assert page['truncated'] == (page['next_offset'] is not None)
        if page['next_offset'] is None:
            break
        assert page['next_offset'] == offset + 2
        offset = page['next_offset']
    assert ids == expected
    assert service.list() == before
    beyond = (await registry.execute(tool, {**arguments, 'offset': 100}))['result']
    assert beyond[key] == [] and beyond['next_offset'] is None
    empty_args = {'filters': {'part_number': 'NONE'}} if tool == 'search_parts' else {'query': 'NONE'}
    empty = (await registry.execute(tool, empty_args))['result']
    assert empty[key] == [] and empty['count'] == 0 and empty['next_offset'] is None


def power(part, value):
    return {'name': 'rated_power', 'value': value, 'basis': 'rated',
            'conditions': {'ambient_temperature': '25 °C'},
            'evidence': {'kind': 'source', 'excerpt': f'Rated power {value} at 25 °C.',
                         'url': 'https://example.com/datasheet.pdf', 'page': 1,
                         'sha256': 'a' * 64, 'retrieved_at': '2026-10-10T12:00:00+00:00',
                         'part_number': part.part_number}}


async def test_specification_pages_preserve_evidence_and_both_groups(service):
    parts = sorted(service.list(), key=lambda p: p.id)
    for part in parts[:3]:
        service.stage_specifications(part, [power(part, '0.5 W')])
        service.apply_specification_review(part.id)
    service.stage_specifications(parts[3], [power(parts[3], '0.125 W')])
    service.apply_specification_review(parts[3].id)  # known failure is excluded
    service.stage_specifications(parts[4], [power(parts[4], '1 W')])  # pending remains incomplete
    query = {'filters': {'part_category': 'resistor'}, 'requirements': [{
        'name': 'rated_power', 'value': '0.25 W', 'basis': 'rated',
        'conditions': {'ambient_temperature': '25 °C'}, 'comparison': 'gte',
    }], 'limit': 2}
    registry = PartsBinToolRegistry(service)
    matches, incomplete, offset = [], [], 0
    while True:
        response = await registry.execute('search_parts', {**query, 'offset': offset})
        assert response['ok']
        page = response['result']
        assert page['match_count'] == 3 and page['incomplete_count'] == 3
        assert len(page['matches']) <= 2 and len(page['incomplete']) <= 2
        for match in page['matches']:
            assert match['supporting_facts'] == [power(parts[match['part']['id'] - 1], '0.5 W')]
        matches.extend(m['part']['id'] for m in page['matches'])
        incomplete.extend(m['part']['id'] for m in page['incomplete'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert matches == [p.id for p in parts[:3]]
    assert incomplete == [p.id for p in parts[4:]]
    # An uneven pair of streams must continue after one group is exhausted.
    service.stage_specifications(parts[2], [power(parts[2], '0.125 W')])
    service.apply_specification_review(parts[2].id)
    first = (await registry.execute('search_parts', query))['result']
    assert first['next_offset'] == 2
    last = (await registry.execute('search_parts', query | {'offset': 2}))['result']
    assert last['matches'] == [] and len(last['incomplete']) == 1
    assert last['next_offset'] is None


@pytest.mark.parametrize('offset', [-1, True, 1.5, '2', None])
async def test_offset_validation_in_domain_and_tool(service, offset):
    with pytest.raises(DomainError, match='offset'):
        service.search_page(SearchPartsRequest(), offset=offset)
    for tool, args in [('search_parts', {}), ('search_candidates', {'query': 'candidate'})]:
        response = await PartsBinToolRegistry(service).execute(tool, args | {'offset': offset})
        assert response['error']['code'] == 'invalid_input'


@pytest.mark.parametrize('limit', [0, 101, True, 1.5])
def test_domain_page_limit_validation(service, limit):
    with pytest.raises(DomainError, match='limit'):
        service.search_page(SearchPartsRequest(), limit=limit)


async def test_agent_can_follow_pages_without_inventory_in_initial_prompt(service, tmp_path):
    from agent_runtime import ApprovalEngine, ModelTurn, OpenAIResponsesRuntime, ToolCall
    from db.conversations import SQLiteConversationRepository

    class Transport:
        def __init__(self):
            self.requests = []
            self.offset = 0
        async def complete(self, request):
            self.requests.append(request)
            if self.offset == 0:
                assert request.exchanges == ()
                assert 'EXACT-7,215' not in request.system
            if self.offset >= 7:
                return ModelTurn('Retrieved all seven stock records.')
            arguments = {'filters': {'part_category': 'resistor'}, 'limit': 2, 'offset': self.offset}
            if self.offset:
                assert request.exchanges[-1]['result']['result']['next_offset'] == self.offset
            self.offset += 2
            return ModelTurn(tool_calls=(ToolCall('search_parts', arguments),))

    transport = Transport()
    runtime = OpenAIResponsesRuntime(transport, registry=PartsBinToolRegistry(service),
        store=SQLiteConversationRepository(tmp_path / 'conversations.db'), approvals=ApprovalEngine(service.repository))
    result = await runtime.run('pages', 'List all resistors')
    assert result.status == 'completed'
    pages = [event.data['result']['result'] for event in result.events if event.kind == 'tool_result']
    assert [p['id'] for page in pages for p in page['parts']] == list(range(1, 8))
    assert all(len(page['parts']) <= 2 for page in pages)
