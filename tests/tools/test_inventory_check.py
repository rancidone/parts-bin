import pytest

from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, PartFields, PartsBinService
from tools import PartsBinToolRegistry


@pytest.fixture
def registry(tmp_path):
    return PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db')))


def add(registry, number, quantity, *, package=None, value=None, category='IC'):
    return registry.service.add_part(AddPartRequest(PartFields(
        part_category=category, profile='passive' if value else 'discrete_ic',
        part_number=number, quantity=quantity, package=package, value=value)))


async def test_bom_reports_stock_shortages_and_exact_missing_identity(registry):
    first = add(registry, 'PBSS5350T,215', 8)
    add(registry, 'LM358N', 0)
    add(registry, 'NE5532P', 2)
    registry.service.repository.inventory.save_pending_review(first.id, {'part_number': 'PENDING'}, [])
    before = registry.service.list()
    result = await registry.execute('check_inventory', {'items': [
        {'filters': {'part_number': 'PBSS5350T,215'}, 'quantity': 4},
        {'filters': {'part_number': 'PBSS5350T,115'}},
        {'filters': {'part_number': 'LM358N'}, 'quantity': 1},
        {'filters': {'part_number': 'NE5532P'}, 'quantity': 3},
        {'filters': {'part_number': 'PBSS5350T,215'}},
        {'filters': {'part_number': 'PENDING'}},
    ]})
    assert result['ok']
    rows = result['result']['items']
    assert [row['status'] for row in rows] == [
        'sufficient', 'missing', 'out_of_stock', 'insufficient', 'in_stock', 'missing']
    assert [row['shortage'] for row in rows] == [0, None, 1, 1, None, None]
    assert rows[0]['parts'][0]['part_number'] == 'PBSS5350T,215'
    assert result['result']['stock_reserved'] is False
    assert registry.service.list() == before
    assert registry.service.list_pending_reviews()[first.id]['fields']['part_number']['value'] == 'PENDING'


async def test_generic_bom_does_not_sum_package_variants(registry):
    add(registry, None, 2, value='10k', category='resistor', package='0402')
    add(registry, None, 3, value='10k', category='resistor', package='0603')
    result = await registry.execute('check_inventory', {'items': [
        {'filters': {'part_category': 'resistor', 'value': '10 kΩ'}, 'quantity': 4},
        {'filters': {'part_category': 'resistor', 'value': '10000r', 'package': '0603'}, 'quantity': 3},
    ]})
    rows = result['result']['items']
    assert rows[0]['status'] == 'insufficient'
    assert rows[0]['max_available_quantity'] == 3
    assert rows[0]['shortage'] == 1
    assert rows[0]['parts'][0]['package'] == '0603'
    assert rows[0]['match_count'] == 2
    assert rows[0]['truncated']
    assert rows[1]['status'] == 'sufficient'


async def test_large_list_has_one_result_per_item_and_bounded_matches(registry):
    for index in range(8):
        add(registry, f'R-{index}', index, value='10k', category='resistor', package=f'P{index}')
    items = [{'filters': {'part_number': f'UNKNOWN-{index}'}} for index in range(99)]
    items.append({'filters': {'part_category': 'resistor', 'value': '10k'}, 'quantity': 7})
    rows = []
    offset = 0
    while offset is not None:
        result = await registry.execute('check_inventory', {'items': items, 'offset': offset})
        assert result['result']['count'] == 100
        assert len(result['result']['items']) <= 20
        rows.extend(result['result']['items'])
        offset = result['result']['next_offset']
    assert len(rows) == 100
    assert [row['index'] for row in rows] == list(range(100))
    assert all(row['status'] == 'missing' for row in rows[:-1])
    assert rows[-1]['match_count'] == 8
    assert len(rows[-1]['parts']) == 1
    assert rows[-1]['truncated']
    assert rows[-1]['parts'][0]['quantity'] == 7
    assert rows[-1]['status'] == 'sufficient'


@pytest.mark.parametrize('items', [
    [], [{'filters': {'part_number': 'A'}}] * 101,
    [{'filters': {}}], [{'filters': {'package': '0603'}}],
    [{'filters': {'part_number': '   '}}],
    [{'filters': {'part_number': 'A'}, 'quantity': True}],
    [{'filters': {'part_number': 'A'}, 'quantity': 0}],
    [{'filters': {'part_number': 'A'}, 'quantity': -1}],
    [{'filters': {'part_number': 'A', 'sql': 'SELECT *'}}],
    [{'filters': {'part_number': 'A'}, 'unknown': 1}],
])
async def test_invalid_bom_lines_are_rejected(registry, items):
    result = await registry.execute('check_inventory', {'items': items})
    assert not result['ok']
    assert result['error']['code'] == 'invalid_input'
