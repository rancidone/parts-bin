"""Reviewed facts remain searchable through the bot contract, across restarts."""

import pytest

from tests.storage import TestRepository
from domain import PartsBinService
from domain.specifications import contract
from ingestion.electrical_source import facts_schema
from tests.domain.test_specifications import fact, requirement, setup
from tools import PartsBinToolRegistry


CASES = [
    ('linear_regulator', 'LDO', 'dropout_voltage', '250 mV', '0.25 V', 'maximum',
     {'output_current': '600 mA', 'output_voltage': '3.3 V', 'ambient_temperature': '25 °C'}),
    ('buck_converter', 'buck converter', 'switching_frequency', '1.5 MHz', '1500 kHz', 'typical',
     {'input_voltage': '3.6 V', 'output_voltage': '1.8 V', 'output_current': '500 mA', 'ambient_temperature': '25 °C'}),
    ('led', 'LEDs', 'luminous_intensity', '1.6 mcd', '0.0016 cd', 'minimum',
     {'forward_current': '10 mA', 'ambient_temperature': '25 °C'}),
    ('led', 'light emitting diode', 'maximum_dominant_wavelength', '625 nm', '0.625 µm', 'maximum',
     {'forward_current': '10 mA', 'ambient_temperature': '25 °C'}),
]


@pytest.mark.parametrize('stored,alias,name,value,query_value,basis,conditions', CASES)
async def test_bot_contract_review_restart_and_qualified_search(tmp_path, stored, alias, name, value, query_value, basis, conditions):
    repository, service, part = setup(tmp_path, stored)
    registry = PartsBinToolRegistry(service)
    response = await registry.execute('get_specification_contract', {'category': alias})
    assert response['result']['fields'][name]['basis'] == basis
    assert name in facts_schema(stored)['required']
    proposed = fact(name, value, basis, conditions=conditions)
    args = {'filters': {'part_category': alias}, 'requirements': [requirement(proposed, query_value)], 'minimum_quantity': 4}
    service.stage_specifications(part, [proposed])
    assert (await registry.execute('search_parts', args))['result']['incomplete_count'] == 1
    restarted = PartsBinService(TestRepository(repository.isolation_key))
    restarted.apply_specification_review(part.id)
    registry = PartsBinToolRegistry(restarted)
    result = (await registry.execute('search_parts', args))['result']
    assert result['match_count'] == 1 and result['incomplete_count'] == 0
    assert result['matches'][0]['supporting_facts'] == [proposed]
    assert restarted.list()[0].quantity == 4
    for key in conditions:
        incomplete = {**proposed, 'conditions': {k: v for k, v in conditions.items() if k != key}}
        restarted.stage_specifications(restarted.list()[0], [incomplete])
        restarted.apply_specification_review(part.id)
        args['requirements'] = [requirement(proposed, query_value)]
        assert (await registry.execute('search_parts', args))['result']['incomplete_count'] == 1


@pytest.mark.parametrize('category', ['linear_regulator', 'buck_converter'])
async def test_operating_input_range_does_not_use_stress_limit(tmp_path, category):
    _, service, part = setup(tmp_path, category)
    conditions = {'ambient_temperature': '-40 to 85 °C'}
    facts = [fact('minimum_input_voltage', '2.7 V', 'operating_minimum', conditions=conditions),
             fact('maximum_input_voltage', '5.5 V', 'operating_maximum', conditions=conditions),
             fact('absolute_maximum_input_voltage', '7 V', 'absolute_maximum', conditions=conditions)]
    service.stage_specifications(part, facts)
    service.apply_specification_review(part.id)
    registry = PartsBinToolRegistry(service)
    args = {'filters': {'part_category': category}, 'requirements': [
        requirement(facts[0], '6 V', 'lte'), requirement(facts[1], '6 V', 'gte')]}
    assert (await registry.execute('search_parts', args))['result']['match_count'] == 0
    assert not contract('voltage regulator')['supported']  # Unspecified topology is ambiguous.


def test_explicit_condition_si_equivalence_preserves_relations_and_units():
    from domain.specification_conditions import same_conditions
    assert same_conditions({'forward_current': '10 mA'}, {'forward_current': '0.01 A'})
    assert not same_conditions({'forward_current': '10 mA'}, {'forward_current': '10 A'})
    assert not same_conditions({'input_voltage': 'VIN'}, {'input_voltage': '3.6 V'})
    assert not same_conditions({'forward_current': '10 mA'}, {})
    assert not same_conditions({'forward_current': '10 mA'}, {'output_current': '10 mA'})
