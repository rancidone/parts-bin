from copy import deepcopy
from unittest.mock import patch

import pytest

from agent_runtime.approval import ApprovalEngine
from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, AddStockRequest, DomainError, GetPartRequest, PartFields, PartsBinService, SearchPartsRequest, UpdatePartRequest
from domain.specifications import SPECIFICATIONS, SpecificationDefinition, contract, numeric_value, validate_facts
from domain.repositories import RepositoryConflict
from tools import PartsBinToolRegistry


CASES = [
    ('resistor', 'rated_power', '250 mW', '0.25 W', 'rated'),
    ('capacitor', 'rated_voltage', '50 V', '0.05 kV', 'rated'),
    ('bjt', 'continuous_collector_current', '500 mA', '0.5 A', 'absolute_maximum_continuous'),
    ('mosfet', 'on_resistance', '100 mΩ', '0.1 Ω', 'maximum'),
    ('diode', 'forward_voltage', '700 mV', '0.7 V', 'maximum'),
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
    if category == 'bjt':
        proposed['conditions'] = {'ambient_temperature': '25 °C'}
    elif category == 'mosfet':
        proposed['conditions'] = {'gate_source_voltage': '10 V', 'drain_current': '500 mA', 'junction_temperature': '25 °C'}
    elif category == 'diode':
        proposed['conditions'] = {'forward_current': '10 mA', 'junction_temperature': '25 °C'}
    elif category == 'capacitor':
        proposed['conditions'] = {'rating_temperature': '85 °C', 'current_type': 'DC'}
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
    repository, service, part = setup(tmp_path, 'photodiode')
    assert service.get(GetPartRequest(part.id)).part_category == 'photodiode'
    assert contract('photodiode')['supported'] is False
    monkeypatch.setitem(SPECIFICATIONS, 'photodiode', {'forward_voltage': SpecificationDefinition('V', 'maximum')})
    assert contract('photodiode')['supported'] is True
    proposed = fact('forward_voltage', '700 mV', 'maximum')
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    assert search(service, 'photodiode', [requirement(proposed, '0.7 V', 'lte')])['match_count'] == 1


@pytest.mark.parametrize('raw,unit', [('1 A', 'V'), ('1', 'W'), ('NaN W', 'W'), ('1e3 W', 'W'), ('-1 W', 'W'), ('1 MW', '%')])
def test_invalid_or_incompatible_units(raw, unit):
    with pytest.raises(DomainError):
        numeric_value(raw, unit)


def test_si_prefix_case_is_significant():
    assert numeric_value('1 MΩ', 'Ω') == 1_000_000
    assert numeric_value('1 mΩ', 'Ω') < 1


@pytest.mark.parametrize('value', ['0.5 V/µs', '0.5 V/μs', '0.5 V/us', '0.5 MV/s', '500 mV/us'])
def test_slew_rate_voltage_and_time_prefixes(value):
    assert numeric_value(value, 'V/s') == 500_000


@pytest.mark.parametrize('value', ['0.5 V', '0.5 V/ms/s', '0.5 V/US', '-0.5 V/us'])
def test_invalid_slew_rate_units(value):
    with pytest.raises(DomainError):
        numeric_value(value, 'V/s')


@pytest.mark.parametrize('category', ['op amp', 'opamps', 'op-amp', 'op_amp', 'operational amplifier'])
def test_opamp_aliases_share_the_contract(category):
    assert contract(category)['fields'] == contract('operational amplifier')['fields']
    assert contract(category)['supported']
    assert not contract('audio amplifier')['supported']


def test_opamp_operating_range_review_restart_and_missing_facts(tmp_path):
    repository, service, part = setup(tmp_path, 'op_amp', number='TEST-OP-B-TR')
    qualified = {'supply_convention': 'total rail-to-rail', 'ambient_temperature': '-40 to 85 °C'}
    proposed = [
        fact('minimum_supply_voltage', '3 V', 'operating_minimum', number=part.part_number, conditions=qualified),
        fact('maximum_supply_voltage', '36 V', 'operating_maximum', number=part.part_number, conditions=qualified),
        fact('absolute_maximum_supply_voltage', '40 V', 'absolute_maximum', number=part.part_number, conditions=qualified),
    ]
    # A 5 V supply must fit both endpoints; a stress rating cannot satisfy them.
    query = [requirement(proposed[0], '5 V', 'lte'), requirement(proposed[1], '5 V', 'gte')]
    service.stage_specifications(part, proposed)
    assert search(service, 'opamps', query)['incomplete_count'] == 1
    restarted = PartsBinService(SQLitePartsBinRepository(repository.database))
    restarted.apply_specification_review(part.id)
    assert search(restarted, 'operational amplifier', query)['match_count'] == 1
    assert search(restarted, 'op amp', [requirement(proposed[1], '38 V')])['match_count'] == 0
    missing = fact('gain_bandwidth_product', '1 MHz', 'typical', conditions={
        'supply_voltage': '5 V', 'ambient_temperature': '25 °C'})
    found = search(restarted, 'op amp', query + [requirement(missing)])
    assert found['incomplete'][0]['missing_or_unqualified'] == ['gain_bandwidth_product']
    stored = restarted.get(GetPartRequest(part.id))
    assert {key: value for key, value in vars(stored).items() if key != 'updated_at'} == {
        key: value for key, value in vars(part).items() if key != 'updated_at'}
    assert repository.inventory.specifications(part.id) == proposed


@pytest.mark.parametrize('name,value,basis,qualified', [
    ('input_offset_voltage', '3 mV', 'maximum_magnitude',
     {'supply_voltage': '5 V', 'ambient_temperature': '25 °C', 'common_mode_voltage': '2.5 V'}),
    ('input_bias_current', '35 nA', 'maximum_magnitude',
     {'supply_voltage': '5 V', 'ambient_temperature': '25 °C', 'common_mode_voltage': '2.5 V'}),
    ('gain_bandwidth_product', '1.2 MHz', 'typical',
     {'supply_voltage': '5 V', 'ambient_temperature': '25 °C'}),
    ('slew_rate', '0.5 V/µs', 'typical',
     {'supply_voltage': '5 V', 'ambient_temperature': '25 °C', 'closed_loop_gain': '1 ratio'}),
    ('quiescent_current', '300 µA', 'typical',
     {'supply_voltage': '5 V', 'ambient_temperature': '25 °C', 'current_scope': 'per amplifier'}),
])
def test_opamp_qualified_facts_and_missing_conditions(tmp_path, name, value, basis, qualified):
    repository, service, part = setup(tmp_path, 'op amp')
    proposed = fact(name, value, basis, conditions=qualified)
    query = [requirement(proposed, comparison='eq')]
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    assert search(service, 'opamp', query)['match_count'] == 1
    # Matching two equally incomplete mappings must not confirm a requirement.
    proposed['conditions'].pop('ambient_temperature')
    service.stage_specifications(part, [proposed])
    service.apply_specification_review(part.id)
    result = search(service, 'opamp', [requirement(proposed)])
    assert result['incomplete'][0]['missing_or_unqualified'] == [name]


@pytest.mark.parametrize('name,value,basis', [
    ('maximum_supply_voltage', '40 V', 'absolute_maximum'),
    ('absolute_maximum_supply_voltage', '36 V', 'operating_maximum'),
    ('input_offset_voltage', '0.3 mV', 'typical'),
    ('input_bias_current', '10 nA', 'typical'),
    ('gain_bandwidth_product', '1.2 MHz', 'minimum'),
    ('slew_rate', '0.5 V/us', 'minimum'),
])
def test_opamp_bases_cannot_be_promoted_or_substituted(name, value, basis):
    with pytest.raises(DomainError, match='requires basis'):
        validate_facts('op amp', [fact(name, value, basis)], part_number='TEST-EXACT')


def test_opamp_evidence_preserves_exact_grade_and_shipping_suffix():
    proposed = fact('input_offset_voltage', '2 mV', 'maximum_magnitude', number='TEST-OP-BA-TR')
    with pytest.raises(DomainError, match='exact ordering code'):
        validate_facts('op amp', [proposed], part_number='TEST-OP-B-TR')


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
    pulse = fact('pulsed_drain_current', '10 A', 'absolute_maximum_pulsed',
                 conditions={'pulse_duration': '10 µs', 'case_temperature': '25 °C'})
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
