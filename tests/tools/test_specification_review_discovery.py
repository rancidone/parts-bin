"""Electrical review discovery stays bounded and separate from accepted facts."""

import pytest

from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, DomainError, PartFields, PartsBinService
from tools import PartsBinToolRegistry


async def test_discovery_pages_filters_and_preserves_review_evidence(tmp_path):
    database = tmp_path / 'parts.db'
    service = PartsBinService(SQLitePartsBinRepository(database))
    identifiers = []
    proposed = {'name': 'tolerance', 'value': '1 %', 'basis': 'maximum', 'conditions': {},
                'evidence': {'kind': 'user_assertion', 'excerpt': 'Marked 1%.'}}
    for index in range(5):
        part = service.add_part(AddPartRequest(PartFields(
            'resistor', 'passive', 4, part_number=f'EXACT-{index}-F', value='10k')))
        identifiers.append(part.id)
        service.stage_specifications(part, [proposed])
    # A metadata-only review must not appear in electrical discovery.
    other = service.add_part(AddPartRequest(PartFields('capacitor', 'passive', 2, value='1uF')))
    service.repository.inventory.save_pending_review(other.id, {'description': 'Proposal'}, [])
    restarted = PartsBinService(SQLitePartsBinRepository(database))
    registry = PartsBinToolRegistry(restarted)
    before = restarted.repository.inventory.specification_reviews()
    first = (await registry.execute('list_pending_specification_reviews', {'limit': 2}))['result']
    assert first == {'reviews': [
        {'part_id': identifier, 'part_category': 'resistor', 'part_number': f'EXACT-{index}-F',
         'fact_names': ['tolerance']} for index, identifier in enumerate(identifiers[:2])
    ], 'count': 5, 'truncated': True, 'next_offset': 2}
    collected = list(first['reviews'])
    offset = first['next_offset']
    while offset is not None:
        page = (await registry.execute('list_pending_specification_reviews', {'limit': 2, 'offset': offset}))['result']
        collected.extend(page['reviews'])
        offset = page['next_offset']
    assert [row['part_id'] for row in collected] == identifiers
    for filters, count in [
        ({'part_category': 'resistor'}, 5), ({'part_category': 'capacitor'}, 0),
        ({'part_number': 'EXACT-0-F'}, 1), ({'part_number': 'EXACT-0'}, 0),
        ({'part_id': identifiers[0]}, 1), ({'part_id': other.id}, 0),
        ({'part_id': identifiers[0], 'part_number': 'EXACT-1-F'}, 0),
    ]:
        result = await registry.execute('list_pending_specification_reviews', filters)
        assert result['ok'] and result['result']['count'] == count
    detail = (await registry.execute('get_specifications', {'part_id': identifiers[0]}))['result']
    assert detail['facts'] == []
    assert detail['pending_review']['facts'] == [proposed]
    assert restarted.repository.inventory.specification_reviews() == before
    assert all(part.quantity == 4 for part in restarted.list() if part.id in identifiers)
    restarted.apply_specification_review(identifiers[0])
    restarted.reject_specification_review(identifiers[1])
    remaining = (await registry.execute('list_pending_specification_reviews', {}))['result']
    assert remaining['count'] == 3
    assert [row['part_id'] for row in remaining['reviews']] == identifiers[2:]
    assert restarted.get_specifications(identifiers[0])['facts'] == [proposed]
    beyond = (await registry.execute('list_pending_specification_reviews', {'offset': 100}))['result']
    assert beyond == {'reviews': [], 'count': 3, 'truncated': False, 'next_offset': None}


@pytest.mark.parametrize('arguments', [
    {'limit': 0}, {'limit': 101}, {'limit': True}, {'offset': -1}, {'offset': False},
    {'part_id': 0}, {'part_id': True}, {'part_category': ''}, {'part_category': ' '},
    {'part_number': ''}, {'part_number': 1},
])
async def test_discovery_validates_tool_and_domain_inputs(tmp_path, arguments):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    result = await PartsBinToolRegistry(service).execute('list_pending_specification_reviews', arguments)
    assert result['error']['code'] == 'invalid_input'
    with pytest.raises(DomainError):
        service.specification_review_page(**arguments)
