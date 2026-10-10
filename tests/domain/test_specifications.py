from copy import deepcopy
from unittest.mock import patch

import pytest

from agent_runtime.approval import ApprovalEngine
from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, AddStockRequest, DomainError, GetPartRequest, PartFields, PartsBinService, SearchPartsRequest, UpdatePartRequest
from domain.specifications import SPECIFICATIONS, SpecificationDefinition, contract, numeric_value
from domain.repositories import RepositoryConflict
from tools import PartsBinToolRegistry


CASES = [
    ('resistor', 'rated_power', '250 mW', '0.25 W', 'rated'),
    ('capacitor', 'rated_voltage', '50 V', '0.05 kV', 'rated'),
    ('bjt', 'continuous_collector_current', '500 mA', '0.5 A', 'absolute_maximum_continuous'),
    ('mosfet', 'on_resistance', '100 mΩ', '0.1 Ω', 'maximum'),
    ('inductor', 'saturation_current', '500 mA', '0.5 A', 'saturation'),
    ('transformer', 'rated_apparent_power', '10 VA', '0.01 kVA', 'rated'),
    ('switch', 'rated_current', '500 mA', '0.5 A', 'rated'),
]


def fact(name='rated_power', value='250 mW', basis='rated', *, number='TEST-EXACT', kind='source', conditions=None):
    evidence = {'kind': kind, 'excerpt': 'Synthetic datasheet passage: rated power 250 mW at 25 °C.'}
    if kind == 'source':
        evidence.update(url='https://example.com/datasheet.pdf', page=2, sha256='a' * 64,
                        retrieved_at='2026-10-10T12:00:00+00:00', part_number=number)
    return dict(name=name, value=value, basis=basis,
                conditions={'ambient_temperature': '25 °C'} if conditions is None else conditions, evidence=evidence)


def requirement(proposed, value=None, comparison='gte'):
    return {key: deepcopy(proposed[key]) for key in ('name', 'basis', 'conditions')} | {
        'value': value or proposed['value'], 'comparison': comparison,
    }


def setup(tmp_path, category='resistor', number='TEST-EXACT', quantity=4):
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    service = PartsBinService(repository)
    part = service.add_part(AddPartRequest(PartFields(category, 'discrete_ic', quantity,
                                                     part_number=number, package='TEST-PACKAGE')))
    return repository, service, part


def search(service, category, requirements, **kwargs):
    return service.search_specifications(SearchPartsRequest({'part_category': category}, minimum_quantity=4), requirements, **kwargs)


@pytest.mark.parametrize('category,name,stored,requested,basis', CASES)
def test_each_initial_category_review_and_inclusive_search(tmp_path, category, name, stored, requested, basis):
    repository, service, part = setup(tmp_path, category)
    proposed = fact(name, stored, basis)
    service.stage_specifications(part, [proposed])
    query = [requirement(proposed, requested)]
    pending = search(service, category, query)
    assert pending['matches'] == []
    assert pending['incomplete'][0]['missing_or_unqualified'] == [name]
    assert service.get(GetPartRequest(part.id)) == part
    # Pending review and source passages survive adapter reconstruction.
    restarted = PartsBinService(SQLitePartsBinRepository(repository.database))
    restarted.apply_specification_review(part.id)
    found = search(restarted, category, query)
    assert found['match_count'] == 1 and found['incomplete_count'] == 0
    assert found['matches'][0]['supporting_facts'] == [proposed]
    assert restarted.get(GetPartRequest(part.id)).quantity == 4
    assert repository.inventory.specifications(part.id) == [proposed]
    assert repository.inventory.specification_reviews() == {}


def test_nonexhaustive_catalog_and_extensible_definitions(tmp_path, monkeypatch):
    repository, service, part = setup(tmp_path, 'diode')
    assert service.get(GetPartRequest(part.id)).part_category == 'diode'
    assert contract('diode')['supported'] is False
    monkeypatch.setitem(SPECIFICATIONS, 'diode', {'forward_voltage': SpecificationDefinition('V', 'maximum')})
    assert contract('diode')['supported'] is True
    proposed = fact('forward_voltage', '700 mV', 'maximum')
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    assert search(service, 'diode', [requirement(proposed, '0.7 V', 'lte')])['match_count'] == 1


@pytest.mark.parametrize('raw,unit', [('1 A', 'V'), ('1', 'W'), ('NaN W', 'W'), ('1e3 W', 'W'), ('-1 W', 'W'), ('1 MW', '%')])
def test_invalid_or_incompatible_units(raw, unit):
    with pytest.raises(DomainError):
        numeric_value(raw, unit)


def test_si_prefix_case_is_significant():
    assert numeric_value('1 MΩ', 'Ω') == 1_000_000
    assert numeric_value('1 mΩ', 'Ω') < 1


@pytest.mark.parametrize('change', [
    {'basis': 'operating'}, {'conditions': {'temperature': 25}}, {'value': '1 V'},
    {'quantity': 99}, {'evidence': {'kind': 'source', 'excerpt': 'No traceable source'}},
])
def test_invalid_facts_do_not_stage(tmp_path, change):
    repository, service, part = setup(tmp_path)
    with pytest.raises(DomainError):
        service.stage_specifications(part, [fact() | change])
    assert repository.inventory.specification_reviews() == {}
    assert service.get(GetPartRequest(part.id)) == part


@pytest.mark.parametrize('key,value', [('part_number', 'TEST'), ('page', True), ('sha256', 'bad'),
                                      ('retrieved_at', '2026-10-10'), ('url', 'http://example.com/a')])
def test_invalid_source_or_wrong_ordering_code_is_not_staged(tmp_path, key, value):
    repository, service, part = setup(tmp_path)
    proposed = fact()
    proposed['evidence'][key] = value
    with pytest.raises(DomainError):
        service.stage_specifications(part, [proposed])
    assert repository.inventory.specification_reviews() == {}


def test_assertions_conditions_and_pulsed_ratings_never_confirm_different_requirement(tmp_path):
    _, service, part = setup(tmp_path, 'mosfet')
    pulse = fact('pulsed_drain_current', '10 A', 'absolute_maximum_pulsed')
    assertion = fact('on_resistance', '0.1 Ω', 'maximum', kind='user_assertion')
    service.stage_specifications(part, [pulse, assertion])
    service.apply_specification_review(part.id)
    continuous = fact('continuous_drain_current', '1 A', 'absolute_maximum_continuous')
    query = [requirement(continuous), requirement(assertion)]
    result = search(service, 'mosfet', query)
    assert result['matches'] == []
    assert result['incomplete'][0]['missing_or_unqualified'] == ['continuous_drain_current', 'on_resistance']
    omitted = requirement(pulse)
    omitted['conditions'] = {}
    assert search(service, 'mosfet', [omitted])['incomplete_count'] == 1
    assert search(service, 'mosfet', [requirement(pulse)])['match_count'] == 1


def test_known_failing_constraint_excludes_candidate_and_stock_applies_before_limit(tmp_path):
    _, service, first = setup(tmp_path)
    for number, qty, power in [('LOW-STOCK', 3, '1 W'), ('LOW-POWER', 8, '0.125 W'), ('UNKNOWN', 4, None)]:
        part = service.add_part(AddPartRequest(PartFields('resistor', 'passive', qty, part_number=number)))
        if power:
            service.stage_specifications(part, [fact(value=power, number=number)])
            service.apply_specification_review(part.id)
    service.stage_specifications(first, [fact()])
    service.apply_specification_review(first.id)
    result = search(service, 'resistor', [requirement(fact(), '0.25 W')], limit=1)
    assert result['match_count'] == 1 and result['incomplete_count'] == 1
    assert result['matches'][0]['part']['id'] == first.id
    assert result['incomplete'][0]['part']['part_number'] == 'UNKNOWN'
    assert search(service, 'resistor', [requirement(fact(), '0.25 W', 'lte')])['match_count'] == 2


def test_nominal_fact_conflict_and_identity_changes_do_not_rebind_evidence(tmp_path):
    _, service, part = setup(tmp_path)
    part = service.update_part(UpdatePartRequest(part.id, {'value': '10k'}))
    with pytest.raises(DomainError, match='nominal'):
        service.stage_specifications(part, [fact('resistance', '22 kΩ', 'nominal')])
    service.stage_specifications(part, [fact('resistance', '10 kΩ', 'nominal')])
    with pytest.raises(DomainError, match='identity'):
        service.update_part(UpdatePartRequest(part.id, {'part_number': 'ANOTHER'}))
    service.apply_specification_review(part.id)
    with pytest.raises(DomainError, match='identity'):
        service.update_part(UpdatePartRequest(part.id, {'package': 'OTHER'}))
    with pytest.raises(DomainError, match='add_stock'):
        service.add_or_increment(AddPartRequest(PartFields('resistor', 'passive', 2, value='10k',
                                                          part_number=part.part_number, package=part.package)))
    assert service.add_stock(AddStockRequest(part.id, 2)).quantity == 6


def test_competing_staging_and_rejection_preserve_previous_facts(tmp_path):
    _, service, part = setup(tmp_path)
    service.stage_specifications(part, [fact()])
    with pytest.raises(DomainError, match='changed'):
        service.stage_specifications(part, [fact(value='1 W')])
    service.apply_specification_review(part.id)
    current = service.get(GetPartRequest(part.id))
    service.stage_specifications(current, [fact(value='1 W')])
    service.reject_specification_review(part.id)
    assert service.get_specifications(part.id)['facts'] == [fact()]


async def test_approval_restart_duplicate_and_transaction_failure(tmp_path):
    repository, service, part = setup(tmp_path)
    service.stage_specifications(part, [fact()])
    engine = ApprovalEngine(repository)
    request = engine.request('chat', 'apply_specification_review', {'part_id': part.id}, service=service)
    restarted = ApprovalEngine(SQLitePartsBinRepository(repository.database))
    approved = restarted.decide('chat', request.request_id, True)
    registry = PartsBinToolRegistry(service)
    from db.specifications import apply
    def failed(database, part_id):
        apply(database, part_id)
        raise RepositoryConflict('Simulated failure after write')
    with patch('db.specifications.apply', failed):
        assert not (await restarted.execute(approved, registry))['ok']
    assert repository.inventory.specifications(part.id) == []
    assert part.id in repository.inventory.specification_reviews()
    fresh = engine.request('chat', 'apply_specification_review', {'part_id': part.id}, service=service)
    accepted = engine.decide('chat', fresh.request_id, True)
    result = await engine.execute(accepted, registry)
    assert result['ok']
    service.add_stock(AddStockRequest(part.id, 1))
    assert await engine.execute(accepted, registry) == result
    assert service.get(GetPartRequest(part.id)).quantity == 5


async def test_approval_binds_exact_review_and_accepted_facts(tmp_path):
    repository, service, part = setup(tmp_path)
    engine = ApprovalEngine(repository)
    service.stage_specifications(part, [fact()])
    request = engine.request('chat', 'apply_specification_review', {'part_id': part.id}, service=service)
    service.reject_specification_review(part.id)
    service.stage_specifications(part, [fact(value='1 W')])
    result = await engine.execute(engine.decide('chat', request.request_id, True), PartsBinToolRegistry(service))
    assert result['error']['code'] == 'conflict'
    assert repository.inventory.specifications(part.id) == []


def test_new_tables_preserve_existing_data_and_cascade_on_delete(tmp_path):
    repository, service, part = setup(tmp_path)
    service.stage_specifications(part, [fact()])
    service.apply_specification_review(part.id)
    assert PartsBinService(SQLitePartsBinRepository(repository.database)).get_specifications(part.id)['facts'] == [fact()]
    assert service.get(GetPartRequest(part.id)).created_at == part.created_at
    from domain import DeletePartRequest
    service.delete_part(DeletePartRequest(part.id))
    assert repository.inventory.specifications(part.id) == []
    assert repository.inventory.specification_reviews() == {}


def test_combined_resistor_requirements_and_evidence_survive_cache_loss(tmp_path):
    repository, service, part = setup(tmp_path)
    part = service.update_part(UpdatePartRequest(part.id, {'value': '10k', 'package': 'through-hole'}))
    facts = [fact('resistance', '10000 Ω', 'nominal', conditions={}),
             fact('tolerance', '1 %', 'maximum', conditions={}), fact()]
    service.stage_specifications(part, facts)
    requirements = [requirement(facts[0], '10 kΩ', 'eq'), requirement(facts[1], '1 %', 'lte'),
                    requirement(facts[2], '0.25 W', 'gte')]
    query = SearchPartsRequest({'part_category': 'resistor', 'value': '10 kΩ', 'package': 'through-hole'}, 4)
    assert service.search_specifications(query, requirements)['matches'] == []
    service.apply_specification_review(part.id)
    from db.enrichment_cache import SQLiteEnrichmentCache
    SQLiteEnrichmentCache(repository.database)  # Disposable cache is independently initialized.
    from db import persistence
    conn = persistence._connect(repository.database)
    with conn:
        conn.execute('DROP TABLE supplied_enrichment_cache')
    conn.close()
    restored = PartsBinService(SQLitePartsBinRepository(repository.database))
    result = restored.search_specifications(query, requirements)
    assert result['match_count'] == 1
    assert result['matches'][0]['supporting_facts'] == facts
    assert restored.get(GetPartRequest(part.id)).quantity == 4


@pytest.mark.parametrize('requirements', [
    [], [{'name': 'made_up', 'value': '1 W', 'basis': 'rated', 'conditions': {}, 'comparison': 'gte'}],
    [{'name': 'rated_power', 'value': '1 W', 'basis': 'absolute_maximum', 'conditions': {}, 'comparison': 'gte'}],
    [{'name': 'rated_power', 'value': '1 W', 'basis': 'rated', 'conditions': {}, 'comparison': ['gte']}],
])
def test_invalid_requirements_are_domain_errors(tmp_path, requirements):
    _, service, _ = setup(tmp_path)
    with pytest.raises(DomainError):
        search(service, 'resistor', requirements)
