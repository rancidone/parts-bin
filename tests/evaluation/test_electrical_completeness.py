import copy
import json

from evaluation.electrical_completeness import EXPECTATIONS, check_completeness
from evaluation.pdf_ocr import transcript, raster_ocr
from ingestion.errors import EnrichmentError
import pytest


def fixture(case_id):
    case = next(item for item in json.loads(EXPECTATIONS.read_text())['cases'] if item['id'] == case_id)
    candidate = {'source': case['source'], 'facts': [
        {'name': name, 'value': fact['values'][0], 'basis': fact['basis'],
         'conditions': {q['names'][0]: q['values'][0] for q in fact['conditions']}}
        for name, fact in case['facts'].items()]}
    return case, copy.deepcopy(candidate)


def test_source_drift_invalidates_scores():
    case, candidate = fixture('bjt_exact_gain_variant')
    candidate['source']['sha256'] = 'different'
    result = check_completeness(case, candidate, 'bjt')
    assert not result['source_hash_matches_review']
    assert result['value_coverage'] is None and result['qualifier_coverage'] is None


def test_present_values_do_not_hide_missing_governing_conditions():
    case, candidate = fixture('bjt_exact_gain_variant')
    candidate['facts'][1]['conditions'] = {}
    result = check_completeness(case, candidate, 'bjt')
    assert result['value_coverage'] == 1
    assert result['qualifier_coverage'] == .75
    assert 'ambient_temperature' in result['issues']['collector_emitter_voltage'][0]


def test_threshold_endpoints_must_match_bound():
    case, candidate = fixture('mosfet_current_and_gate_conditions')
    candidate['facts'][-1]['conditions']['bound'] = 'maximum'
    result = check_completeness(case, candidate, 'mosfet')
    assert 'value and qualifier pairing mismatch' in result['issues']['gate_threshold_voltage']


def test_current_test_pairs_cannot_be_mixed():
    case, candidate = fixture('inductor_saturation_and_rms')
    candidate['facts'][-2]['conditions']['inductance_drop'] = '30 %'
    result = check_completeness(case, candidate, 'inductor')
    assert 'value and qualifier pairing mismatch' in result['issues']['saturation_current']


def test_application_ratings_are_flagged():
    case, candidate = fixture('transformer_ratio_and_missing_ratings')
    candidate['facts'].append({'name': 'primary_rated_voltage', 'value': '5 V'})
    assert check_completeness(case, candidate, 'transformer')['unsupported_proposals'] == ['primary_rated_voltage']


def test_equivalent_units_compare_without_model_assistance():
    case, candidate = fixture('bjt_exact_gain_variant')
    candidate['facts'][2]['value'] = '0.8 A'
    assert check_completeness(case, candidate, 'bjt')['qualifier_coverage'] == 1


def test_ocr_transcript_keeps_lines_and_separates_recognition_from_truth():
    header = 'block_num\tpar_num\tline_num\tconf\ttext\n'
    text, quality = transcript(header + '1\t1\t1\t99\t22\n1\t1\t1\t50\tuH\n1\t1\t2\t100\t0.74\n')
    assert text == '22 uH\n0.74'
    assert quality['ocr_character_weighted_confidence'] == 87.2
    assert quality['low_confidence_words'] == [{'text': 'uH', 'confidence': 50.0}]
    assert 'not source association' in quality['note']


@pytest.mark.parametrize('pages,resolution', [([1,2,3,4],300),([1,1],300),([1],600)])
def test_ocr_input_limits_precede_rendering(monkeypatch, pages, resolution):
    monkeypatch.setattr('evaluation.pdf_ocr.shutil.which', lambda _: '/fake/tesseract')
    with pytest.raises(EnrichmentError, match='input budget'):
        raster_ocr(b'%PDF-fake',pages,resolution=resolution)
