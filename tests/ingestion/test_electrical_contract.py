"""Source-bound model decisions remain distinct from validated domain facts."""

import pytest

from domain.specifications import contract
from ingestion.electrical_source import resolve_source_metadata, source_fact_records
from ingestion.supplied_source import EnrichmentError, extraction_schema


PASSAGES = [
    {'passage_id': 1, 'page': 4, 'text': 'Recommended operating input 2.7 to 5.5 V.'},
    {'passage_id': 2, 'page': 19, 'text': 'Texas Instruments TLV62565DBVR SOT-23 ordering row.'},
    {'passage_id': 3, 'page': 2, 'text': 'Texas Instruments Incorporated.'},
]


def test_identity_citations_can_only_choose_visible_identity_passages():
    schema = extraction_schema('buck_converter', part_number='TLV62565DBVR',
                               manufacturer='Texas Instruments', passages=PASSAGES)
    fields = schema['properties']['fields']['properties']
    assert fields['part_number']['properties']['evidence']['properties']['passage_id']['enum'] == [2]
    assert fields['manufacturer']['properties']['evidence']['properties']['passage_id']['enum'] == [2, 3]
    assert fields['package'] == fields['description'] == {'type': 'null'}
    facts = schema['properties']['facts']
    assert set(facts['required']) == set(contract('buck_converter')['fields'])
    assert facts['properties']['maximum_input_voltage']['properties']['basis']['enum'] == ['operating_maximum']


@pytest.mark.parametrize('text', ['Coilcraft TA7618-AL_ packaging D or B.',
                                 'Coilcraft TA7618-ALD transformer.'])
def test_incomplete_ordering_prefix_cannot_be_cited_as_standalone_identity(text):
    schema = extraction_schema('transformer', part_number='TA7618-AL',
        passages=[{'passage_id': 1, 'page': 1, 'text': text}])
    assert schema['properties']['fields']['properties']['part_number'] == {'type': 'null'}
    assert 'proposal' not in schema['properties']['outcome']['enum']


def test_each_field_needs_a_decision_but_null_does_not_invent_a_fact():
    decisions = dict.fromkeys(contract('led')['fields'])
    assert source_fact_records(decisions, 'led') == []
    del decisions['minimum_dominant_wavelength']
    with pytest.raises(EnrichmentError, match='every supported field'):
        source_fact_records(decisions, 'led')


def test_identity_and_mismatch_pages_and_quotes_are_owned_by_the_server():
    raw = {'fields': {'manufacturer': {'value': 'Texas Instruments', 'evidence': {'passage_id': 3}}},
           'mismatch_evidence': {'passage_id': 1}}
    result = resolve_source_metadata(raw, PASSAGES)
    assert result['fields']['manufacturer']['evidence'] == {
        'page': 2, 'excerpt': 'Texas Instruments Incorporated.'}
    assert result['mismatch_evidence'] == {'page': 4, 'excerpt': PASSAGES[0]['text']}
    assert raw['fields']['manufacturer']['evidence'] == {'passage_id': 3}


@pytest.mark.parametrize('evidence', [{'passage_id': True}, {'passage_id': 4}, {'passage_id': 0},
    {'passage_id': '2'}, {'page': 19, 'excerpt': 'Invented quotation'},
    {'passage_id': 2, 'excerpt': 'Invented quotation'}])
def test_invalid_or_retyped_identity_citations_are_rejected(evidence):
    with pytest.raises(EnrichmentError, match='supplied passage ID'):
        resolve_source_metadata({'fields': {'part_number': {'value': 'TLV62565DBVR', 'evidence': evidence}}}, PASSAGES)
