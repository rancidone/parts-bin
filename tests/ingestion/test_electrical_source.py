"""Mocked extraction checks, not evidence of live source/model quality."""

import copy
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from db.enrichment_cache import SQLiteEnrichmentCache
from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, GetPartRequest, PartFields, PartsBinService
from ingestion import supplied_source as source
from ingestion.datasheet import DatasheetFetcher
from tools import ApprovalReceipt, PartsBinToolRegistry, ToolExecutionContext


URL = 'https://www.vishay.com/docs/example.pdf'
NUMBER = 'EXACT-10K-F'
PASSAGE = 'EXACT-10K-F Vishay. Resistance 10 kΩ; tolerance 1%; rated power 0.25 W at ambient temperature 70 °C.'
DOCUMENT = source.Document(URL, 'a' * 64, '2026-10-10T00:00:00+00:00', (PASSAGE,))


def candidate(number=NUMBER, passage=PASSAGE, facts=None):
    return {'outcome': 'proposal', 'clarification': None, 'mismatch_evidence': None, 'fields': {
        'part_number': {'value': number, 'evidence': {'page': 1, 'excerpt': passage}},
        'manufacturer': {'value': 'Vishay', 'evidence': {'page': 1, 'excerpt': passage}},
        'package': None, 'description': None}, 'facts': facts if facts is not None else [
            raw_fact('resistance', '10 kΩ', 'nominal'),
            raw_fact('tolerance', '1 %', 'maximum'),
            raw_fact('rated_power', '0.25 W', 'rated', [{'name': 'ambient_temperature', 'value': '70 °C'}]),
        ]}


def raw_fact(name, value, basis, conditions=None, passage=PASSAGE):
    return {'name': name, 'value': value, 'basis': basis, 'conditions': conditions or [],
            'evidence': {'passage_ids': [1]}}


async def extract(raw, category='resistor', document=DOCUMENT):
    def handler(request):
        body = json.loads(request.content)
        assert body['tools'] == [] and body['store'] is False
        assert json.loads(body['input'])['specification_contract']['category'] == category
        assert 'facts' in body['text']['format']['schema']['required']
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(raw)}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await source.extract(document, NUMBER, 'Vishay', category=category,
                                    api_key='fake', model='test', client=client)


async def test_four_resistors_confirm_only_after_source_review_and_approval(tmp_path):
    repository = SQLitePartsBinRepository(tmp_path / 'parts.db')
    adapter = DatasheetFetcher(api_key='fake', model='test', cache=SQLiteEnrichmentCache(tmp_path / 'cache.db'))
    service = PartsBinService(repository, datasheet_fetcher=adapter)
    part = service.add_part(AddPartRequest(PartFields('resistor', 'passive', 4, value='10k',
                                                  part_number=NUMBER, manufacturer='Vishay')))
    registry = PartsBinToolRegistry(service, approval_checker=lambda *_: True)
    requirements = [
        {'name': 'resistance', 'comparison': 'eq', 'value': '10 kΩ', 'basis': 'nominal', 'conditions': {}},
        {'name': 'tolerance', 'comparison': 'lte', 'value': '1 %', 'basis': 'maximum', 'conditions': {}},
        {'name': 'rated_power', 'comparison': 'gte', 'value': '0.25 W', 'basis': 'rated',
         'conditions': {'ambient_temperature': '70 °C'}},
    ]
    query = {'filters': {'part_category': 'resistor'}, 'minimum_quantity': 4, 'requirements': requirements}
    extracted = await extract(candidate())
    with patch.object(source, 'retrieve_pdf', AsyncMock(return_value=DOCUMENT)), \
            patch.object(source, 'extract', AsyncMock(return_value=extracted)):
        staged = await registry.execute('ingest_datasheet', {'part_id': part.id, 'source_url': URL})
    assert staged['ok'] and staged['result']['review_staged']
    assert staged['result']['extraction_assessment'] == extracted['extraction_assessment']
    assert staged['result']['facts'] == []
    assert service.get(GetPartRequest(part.id)) == part
    assert (await registry.execute('search_parts', query))['result']['matches'] == []
    args = {'part_id': part.id}
    assert (await registry.execute('apply_specification_review', args))['error']['code'] == 'approval_required'
    # Staged evidence survives restart and disposable-cache loss.
    restarted = PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db')),
                                    approval_checker=lambda *_: True)
    accepted = await restarted.execute('apply_specification_review', args,
        context=ToolExecutionContext(ApprovalReceipt.issue('apply_specification_review', args)))
    assert accepted['ok']
    found = (await restarted.execute('search_parts', query))['result']
    assert found['matches'][0]['part']['quantity'] == 4
    assert found['matches'][0]['supporting_facts'] == extracted['facts']
    assert extracted['facts'][2]['evidence'] == {
        'kind': 'source', 'url': URL, 'sha256': 'a' * 64, 'page': 1, 'excerpt': PASSAGE,
        'retrieved_at': DOCUMENT.retrieved_at, 'part_number': NUMBER}
    other_conditions = copy.deepcopy(query)
    other_conditions['requirements'][2]['conditions'] = {'ambient_temperature': '85 °C'}
    assert (await restarted.execute('search_parts', other_conditions))['result']['matches'] == []


@pytest.mark.parametrize('category,name,value,basis,conditions', [
    ('capacitor', 'rated_voltage', '25 V', 'rated', [{'name': 'temperature', 'value': '85 °C'}]),
    ('bjt', 'pulsed_collector_current', '3 A', 'absolute_maximum_pulsed', [{'name': 'pulse_duration', 'value': '1 ms'}]),
    ('mosfet', 'on_resistance', '20 mΩ', 'maximum', [{'name': 'gate_source_voltage', 'value': '10 V'}]),
    ('inductor', 'saturation_current', '2 A', 'saturation', [{'name': 'inductance_drop', 'value': '30 %'}]),
    ('transformer', 'rated_apparent_power', '10 VA', 'rated', [{'name': 'frequency', 'value': '50 Hz'}]),
    ('switch', 'rated_current', '5 A', 'rated', [{'name': 'load', 'value': 'resistive AC at 250 V'}]),
])
async def test_categories_independently_preserve_source_qualifiers(category, name, value, basis, conditions):
    passage = f'{NUMBER} Vishay. {name} {value}; {basis}; {conditions[0]["name"]} {conditions[0]["value"]}.'
    document = source.Document(URL, 'a' * 64, DOCUMENT.retrieved_at, (passage,))
    proposed = raw_fact(name, value, basis, conditions, passage)
    result = await extract(candidate(passage=passage, facts=[proposed]), category, document)
    fact = result['facts'][0]
    assert (fact['name'], fact['value'], fact['basis']) == (name, value, basis)
    assert fact['conditions'] == {pair['name']: pair['value'] for pair in conditions}
    assert fact['evidence']['excerpt'] == passage


@pytest.mark.parametrize('change', ['basis', 'quote', 'page', 'duplicate', 'condition', 'quantity', 'variant'])
async def test_invalid_electrical_proposals_are_rejected(change):
    raw = candidate()
    if change == 'basis':
        raw['facts'][2]['basis'] = 'absolute_maximum'
    elif change == 'unit':
        raw['facts'][2]['value'] = '250 V'
    elif change == 'quote':
        raw['facts'][2]['evidence']['excerpt'] = 'Invented power evidence'
    elif change == 'page':
        raw['facts'][2]['evidence']['passage_ids'] = [True]
    elif change == 'duplicate':
        raw['facts'].append(raw['facts'][0])
    elif change == 'condition':
        raw['facts'][2]['conditions'] *= 2
    elif change == 'quantity':
        raw['facts'][2]['quantity'] = 99
    else:
        raw['fields']['part_number']['value'] = NUMBER[:-2]
    with pytest.raises(source.EnrichmentError):
        await extract(raw)


@pytest.mark.parametrize('outcome', ['needs_clarification', 'no_match'])
async def test_unresolved_identity_has_no_facts(outcome):
    raw = candidate()
    document = source.Document(URL, 'a' * 64, DOCUMENT.retrieved_at, ('Coilcraft flyback transformer',))
    raw.update(mismatch_evidence={'page': 1, 'excerpt': document.pages[0]} if outcome == 'no_match' else None,
               outcome=outcome, clarification='Which exact ordering variant?', fields=dict.fromkeys(source.FIELDS))
    with pytest.raises(source.EnrichmentError):
        await extract(raw)
    raw['facts'] = []
    result = await extract(raw, document=document)
    assert result['facts'] == [] and result['outcome'] == outcome


async def test_missing_facts_remain_unknown():
    result = await extract(candidate(facts=[]))
    assert result['facts'] == []


async def test_switch_keeps_paired_load_and_complete_test_environment():
    qualified = {
        'rated_voltage': '30 V', 'current_type': 'DC', 'load_type': 'resistive',
        'ambient_temperature': '20 °C', 'ambient_temperature_tolerance': '2 °C',
        'ambient_humidity': '65 %', 'ambient_humidity_tolerance': '5 %',
        'operating_frequency': '30 operations/min', 'operating_force_class': 'low',
    }
    passage = f'{NUMBER} Vishay. Rated current 0.1 A. {qualified}'
    document = source.Document(URL, 'a' * 64, DOCUMENT.retrieved_at, (passage,))
    fact = raw_fact('rated_current', '0.1 A', 'rated',
                    [{'name': name, 'value': value} for name, value in qualified.items()])
    result = await extract(candidate(passage=passage, facts=[fact]), 'switch', document)
    assert result['facts'][0]['conditions'] == qualified
    # Reject oversized candidates instead of silently dropping a qualifier.
    fact['conditions'] += [{'name': f'extra_{i}', 'value': 'unspecified'} for i in range(4)]
    with pytest.raises(source.EnrichmentError, match='conditions'):
        await extract(candidate(passage=passage, facts=[fact]), 'switch', document)


async def test_electrical_evidence_must_be_visible_to_model():
    with patch.object(source, 'select_excerpts', return_value=([{'page': 1, 'text': f'{NUMBER} Vishay.'}], True)):
        raw = candidate()
        for item in raw['fields'].values():
            if item:
                item['evidence']['excerpt'] = f'{NUMBER} Vishay.'
        raw['facts'][0]['evidence']['passage_ids'] = [2]
        with pytest.raises(source.EnrichmentError, match='supplied passage'):
            await extract(raw)


async def test_identity_quote_cannot_be_only_longer_variant():
    longer = PASSAGE.replace(NUMBER, NUMBER + '-TR')
    raw = candidate(passage=longer, facts=[])
    with pytest.raises(source.EnrichmentError, match='exact ordering variant'):
        await extract(raw, document=source.Document(URL, 'a' * 64, DOCUMENT.retrieved_at, (longer,)))


async def test_cache_separates_metadata_and_category_contracts(tmp_path):
    cache = SQLiteEnrichmentCache(tmp_path / 'cache.db')
    with patch.object(source, 'retrieve_pdf', AsyncMock(return_value=DOCUMENT)) as retrieve, \
            patch.object(source, 'extract', AsyncMock(return_value=await extract(candidate()))) as model:
        await source.enrich(NUMBER, 'Vishay', URL, api_key='fake', model='test', cache=cache, category='resistor')
        hit = await source.enrich(NUMBER, 'Vishay', URL, api_key='', model='test', cache=cache, category='resistor')
        assert hit['cache_hit'] and model.await_count == 1
        await source.enrich(NUMBER, 'Vishay', URL, api_key='fake', model='test', cache=cache)
        await source.enrich(NUMBER, 'Vishay', URL, api_key='fake', model='test', cache=cache, category='capacitor')
        assert retrieve.await_count == model.await_count == 3


async def test_unsupported_category_is_rejected_before_retrieval(tmp_path):
    with patch.object(source, 'retrieve_pdf', AsyncMock()) as retrieve:
        with pytest.raises(source.EnrichmentError, match='category'):
            await source.enrich(NUMBER, 'Vishay', URL, api_key='fake', model='test',
                cache=SQLiteEnrichmentCache(tmp_path / 'cache.db'), category='audio amplifier')
    retrieve.assert_not_awaited()


async def test_opamp_extraction_review_keeps_operating_stress_and_typical_facts_distinct(tmp_path):
    # Synthetic source/transport exercise; this does not measure live extraction.
    passage = f'{NUMBER} Vishay operational amplifier. Recommended total supply 3 to 36 V; stress limit 40 V; slew rate typical 0.5 V/µs.'
    heading = 'Ambient -40 to 85 °C for supply ratings. Slew measured at 5 V, 25 °C, gain 1, load 10 kΩ to mid-supply.'
    document = source.Document(URL, 'a' * 64, DOCUMENT.retrieved_at, (passage, heading))
    supply = [{'name': 'supply_convention', 'value': 'total rail-to-rail'},
              {'name': 'ambient_temperature', 'value': '-40 to 85 °C'}]
    facts = [raw_fact('minimum_supply_voltage', '3 V', 'operating_minimum', supply),
             raw_fact('maximum_supply_voltage', '36 V', 'operating_maximum', supply),
             raw_fact('absolute_maximum_supply_voltage', '40 V', 'absolute_maximum', supply),
             raw_fact('slew_rate', '0.5 V/µs', 'typical', [
                 {'name': 'supply_voltage', 'value': '5 V'},
                 {'name': 'ambient_temperature', 'value': '25 °C'},
                 {'name': 'closed_loop_gain', 'value': '1 ratio'},
                 {'name': 'load_resistance', 'value': '10 kΩ'},
                 {'name': 'load_reference', 'value': 'mid-supply'}])]
    for item in facts:
        item['evidence']['passage_ids'] = [1, 2]
    result = await extract(candidate(passage=passage, facts=facts), 'op amp', document)
    assert set(result['extraction_assessment']['missing_fields']) == {
        'input_offset_voltage', 'input_bias_current', 'gain_bandwidth_product', 'quiescent_current'}
    assert result['extraction_assessment']['incomplete_fields'] == {}
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    part = service.add_part(AddPartRequest(PartFields('op_amp', 'discrete_ic', 4, part_number=NUMBER)))
    service.stage_specifications(part, result['facts'])
    restarted = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    restarted.apply_specification_review(part.id)
    assert restarted.get_specifications(part.id)['facts'] == result['facts']
    stored = restarted.get(GetPartRequest(part.id))
    assert {key: value for key, value in vars(stored).items() if key != 'updated_at'} == {
        key: value for key, value in vars(part).items() if key != 'updated_at'}
    assert result['facts'][-1]['conditions']['load_reference'] == 'mid-supply'


async def test_opamp_ambiguous_identity_cannot_stage_facts():
    raw = {'outcome': 'needs_clarification', 'clarification': 'Which grade and shipping suffix?',
           'mismatch_evidence': None, 'fields': {},
           'facts': [raw_fact('maximum_supply_voltage', '36 V', 'operating_maximum')]}
    with pytest.raises(source.EnrichmentError, match='Unresolved identity'):
        await extract(raw, 'op amp')


async def test_opamp_stress_rating_cannot_be_extracted_as_operating_limit():
    raw = candidate(facts=[raw_fact('maximum_supply_voltage', '40 V', 'absolute_maximum')])
    with pytest.raises(source.EnrichmentError, match='requires basis operating_maximum'):
        await extract(raw, 'op amp')


async def test_multiple_source_passages_survive_validation_and_review(tmp_path):
    document = source.Document(URL, 'a' * 64, DOCUMENT.retrieved_at,
        (PASSAGE, 'Rated power measured at ambient temperature 70 °C.'))
    raw = candidate(facts=[raw_fact('rated_power', '0.25 W', 'rated',
                                  [{'name': 'ambient_temperature', 'value': '70 °C'}])])
    raw['facts'][0]['evidence']['passage_ids'] = [1, 2]
    result = await extract(raw, document=document)
    evidence = result['facts'][0]['evidence']
    assert evidence['excerpt'] == PASSAGE
    assert evidence['supporting_passages'] == [{'page': 2, 'excerpt': document.pages[1]}]
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / 'parts.db'))
    part = service.add_part(AddPartRequest(PartFields('resistor', 'passive', 4, value='10k', part_number=NUMBER)))
    service.stage_specifications(part, result['facts'])
    service.apply_specification_review(part.id)
    assert service.get_specifications(part.id)['facts'] == result['facts']


def test_passages_are_verbatim_and_keep_multibyte_budget():
    passages, omitted = source.electrical_passages([{'page': 2, 'text': '🪿' * 5000}])
    assert omitted
    assert sum(len(item['text'].encode()) for item in passages) <= source.MAX_EXCERPT_BYTES
    assert all(len(item['text']) <= 1200 and item['text'] in '🪿' * 5000 for item in passages)


async def test_invalid_electrical_field_does_not_discard_valid_source_facts():
    raw = candidate()
    raw['facts'][2]['value'] = '250 V'
    result = await extract(raw)
    assert [fact['name'] for fact in result['facts']] == ['resistance', 'tolerance']
    assert result['extraction_assessment']['rejected_fields'][0]['name'] == 'rated_power'
