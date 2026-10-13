"""Explain and rank alternatives without weakening exact stock or source rules."""

from copy import deepcopy

import pytest

from db.repository import SQLitePartsBinRepository
from domain import (AddPartRequest, CheckInventoryRequest, InventoryCheckItem, PartFields,
                    PartsBinService, SearchPartsRequest)
from tests.domain.test_specifications import fact, requirement
from tools import PartsBinToolRegistry


def add(service, value='10k', package='0603', quantity=4, category='resistor'):
    return service.add_part(AddPartRequest(PartFields(category, 'passive', quantity, value=value,
        package=package, part_number=f'EXACT-{len(service.list())},215')))


def accept(service, part, power, tolerance=None):
    facts = [fact('rated_power', power, 'rated', number=part.part_number)]
    if tolerance is not None:
        facts.append(fact('tolerance', tolerance, 'maximum', number=part.part_number))
    service.stage_specifications(part, facts)
    service.apply_specification_review(part.id)
    return facts


@pytest.mark.parametrize('category,requested,close,far', [
    ('resistor', '10 kΩ', '9.1k', '20k'),
    ('capacitor', '100 nF', '82n', '220n'),
    ('inductor', '10 µH', '8.2u', '22u'),
])
async def test_nearby_nominal_values_are_explicit_bounded_and_do_not_fill_a_bom(tmp_path, category, requested, close, far):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    farther = add(service, far, category=category)
    nearer = add(service, close, category=category)
    add(service, close, package='0805', category=category)
    add(service, close, quantity=0, category=category)
    add(service, 'unknown', category=category)
    request = {'filters': {'part_category': category, 'value': requested, 'package': '0603'}, 'minimum_quantity': 1, 'limit': 1}
    before = service.list()
    registry = PartsBinToolRegistry(service)
    first = (await registry.execute('search_parts', request))['result']
    assert first['parts'] == [] and first['count'] == 0
    assert first['partial_match_count'] == 2
    assert first['partial_matches'][0]['part']['id'] == nearer.id
    mismatch = first['partial_matches'][0]['filter_mismatches'][0]
    assert mismatch['requested'] == requested and mismatch['actual'] == close
    assert first['partial_matches'][0]['match_kind'] == 'partial'
    assert first['next_offset'] == 1
    second = (await registry.execute('search_parts', {**request, 'offset': 1}))['result']
    assert second['partial_matches'][0]['part']['id'] == farther.id
    assert second['next_offset'] is None
    bom = service.check_inventory(CheckInventoryRequest((InventoryCheckItem(request['filters'], 1),)))
    assert bom['items'][0]['status'] == 'missing'
    assert service.list() == before
    exact_code = (await registry.execute('search_parts', {**request, 'filters': {**request['filters'], 'part_number': nearer.part_number}}))['result']
    assert exact_code['partial_match_count'] == 1
    absent_code = (await registry.execute('search_parts', {**request, 'filters': {**request['filters'], 'part_number': 'EXACT-ABSENT,115'}}))['result']
    assert absent_code['partial_match_count'] == 0


async def test_electrical_and_nominal_partial_matches_rank_and_report_all_misses_after_restart(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    service = PartsBinService(repository)
    perfect = add(service)
    perfect_facts = accept(service, perfect, '0.5 W', '1 %')
    near = add(service)
    near_facts = accept(service, near, '450 mW', '1 %')
    farther = add(service)
    accept(service, farther, '0.25 W', '1 %')
    two_failures = add(service)
    two_facts = accept(service, two_failures, '450 mW', '2 %')
    nominal_near = add(service, '9.9k')
    accept(service, nominal_near, '0.5 W', '1 %')
    incomplete = add(service)
    mixed = add(service)
    accept(service, mixed, '450 mW')
    before = deepcopy(service.list())
    restarted = PartsBinService(SQLitePartsBinRepository(repository.database))
    queries = [requirement(perfect_facts[0], '0.5 W', 'gte'), requirement(perfect_facts[1], '1 %', 'lte')]
    args = {'filters': {'part_category': 'resistor', 'value': '10k', 'package': '0603'},
            'requirements': queries, 'minimum_quantity': 1, 'limit': 2}
    registry = PartsBinToolRegistry(restarted)
    partial, matches, unknown, offset = [], [], [], 0
    while True:
        page = (await registry.execute('search_parts', {**args, 'offset': offset}))['result']
        assert (page['match_count'], page['partial_match_count'], page['incomplete_count']) == (1, 5, 1)
        assert all(len(page[key]) <= 2 for key in ['matches', 'partial_matches', 'incomplete'])
        partial.extend(page['partial_matches'])
        matches.extend(page['matches'])
        unknown.extend(page['incomplete'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert [row['part']['id'] for row in partial] == [nominal_near.id, near.id, farther.id, two_failures.id, mixed.id]
    assert [row['part']['id'] for row in matches] == [perfect.id]
    assert [row['part']['id'] for row in unknown] == [incomplete.id]
    assert partial[1]['failed_requirements'] == [{'requirement': queries[0], 'fact': near_facts[0]}]
    assert partial[1]['supporting_facts'] == [near_facts[1]]
    assert partial[1]['matched_requirement_count'] == 1
    assert partial[3]['failed_requirements'] == [{'requirement': query, 'fact': stored} for query, stored in zip(queries, two_facts)]
    assert partial[4]['missing_or_unqualified'] == ['tolerance']
    assert partial[0]['filter_mismatches'][0]['actual'] == '9.9k'
    assert all(row['match_kind'] == 'partial' for row in partial)
    assert restarted.list() == before
    assert restarted.get_specifications(two_failures.id)['facts'] == two_facts


def test_unqualified_and_asserted_facts_do_not_become_known_failures(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    part = add(service)
    proposed = fact('rated_power', '0.1 W', 'rated', number=part.part_number,
                    conditions={'ambient_temperature': '25 °C'})
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    request = SearchPartsRequest({'part_category': 'resistor'})
    query = {**requirement(proposed, '0.5 W'), 'conditions': {'ambient_temperature': '85 °C'}}
    result = service.search_specifications(request, [query])
    assert result['partial_matches'] == [] and result['incomplete_count'] == 1
    service.stage_specifications(part, [{**proposed, 'evidence': {'kind': 'user_assertion', 'excerpt': 'I think 0.1 W'}}])
    service.apply_specification_review(part.id)
    result = service.search_specifications(request, [requirement(proposed, '0.5 W')])
    assert result['partial_matches'] == [] and result['incomplete_count'] == 1


def test_nearby_search_requires_a_dimension_and_preserves_exact_si_units(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    exact = add(service, '10k')
    nearer = add(service, '9.9k')
    tiny = add(service, '0.01r')
    page = service.search_page(SearchPartsRequest({'part_category': 'resistor', 'value': '10000 Ω'}))
    assert [part.id for part in page['parts']] == [exact.id]
    assert [row['part'].id for row in page['partial_matches']] == [nearer.id, tiny.id]
    assert service.search_page(SearchPartsRequest({'value': '10k'}))['partial_matches'] == []


def test_nominal_ranking_preserves_case_sensitive_units_and_decimal_precision():
    from decimal import localcontext
    from domain.search import nominal_mismatch
    with localcontext() as context:
        context.prec = 3
        mismatch = nominal_mismatch('1 MΩ', '1 mΩ', 'resistor')
        assert mismatch.relative_difference > 0
        tiny = nominal_mismatch('123456.789k', '123456.788k', 'resistor')
        assert 0 < tiny.relative_difference < mismatch.relative_difference
    assert nominal_mismatch('100n', '10 µH', 'capacitor') is None


def test_categorical_difference_is_explicit_and_retains_actual_source_fact(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    part = add(service, '100n', category='capacitor')
    actual = fact('dielectric', 'X5R', 'nominal', number=part.part_number)
    service.stage_specifications(part, [actual])
    service.apply_specification_review(part.id)
    query = requirement(actual, 'C0G', 'eq')
    result = service.search_specifications(SearchPartsRequest({'part_category': 'capacitor'}), [query])
    assert result['matches'] == [] and result['incomplete'] == []
    assert result['partial_matches'][0]['failed_requirements'] == [{'requirement': query, 'fact': actual}]
