import copy
import json

from evaluation.electrical_completeness import EXPECTATIONS, assess_report, check_completeness
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


@pytest.mark.parametrize('case_id,neighbor_offset', [
    ('opamp_b_operating_and_stress_limits', '2 mV'),
    ('opamp_ba_exact_offset_grade', '3 mV'),
])
def test_opamp_neighboring_offset_grade_is_not_a_match(case_id, neighbor_offset):
    case, candidate = fixture(case_id)
    offset = next(fact for fact in candidate['facts'] if fact['name'] == 'input_offset_voltage')
    offset['value'] = neighbor_offset
    result = check_completeness(case, candidate, 'operational amplifier')
    assert 'wrong or unreviewed value' in result['issues']['input_offset_voltage']


def test_opamp_offset_limit_must_pair_with_temperature():
    case, candidate = fixture('opamp_ba_exact_offset_grade')
    offset = next(fact for fact in candidate['facts'] if fact['name'] == 'input_offset_voltage')
    offset['conditions']['ambient_temperature'] = '-40 to 85 °C'
    result = check_completeness(case, candidate, 'operational amplifier')
    assert 'value and qualifier pairing mismatch' in result['issues']['input_offset_voltage']


def test_opamp_present_values_do_not_hide_global_test_conditions_or_current_scope():
    case, candidate = fixture('opamp_b_operating_and_stress_limits')
    for fact in candidate['facts']:
        fact['conditions'].pop('load_reference', None)
        fact['conditions'].pop('current_scope', None)
    result = check_completeness(case, candidate, 'operational amplifier')
    assert result['value_coverage'] == 1
    assert 'missing or unreviewed qualifier: load_reference' in result['issues']['input_offset_voltage']
    assert 'missing or unreviewed qualifier: current_scope' in result['issues']['quiescent_current']


def test_opamp_typical_bandwidth_cannot_be_promoted_to_a_guarantee():
    case, candidate = fixture('opamp_b_operating_and_stress_limits')
    bandwidth = next(fact for fact in candidate['facts'] if fact['name'] == 'gain_bandwidth_product')
    bandwidth['basis'] = 'minimum'
    result = check_completeness(case, candidate, 'operational amplifier')
    assert 'wrong basis' in result['issues']['gain_bandwidth_product']


def test_opamp_stress_voltage_cannot_establish_operating_endpoint():
    case, candidate = fixture('opamp_b_operating_and_stress_limits')
    maximum = next(fact for fact in candidate['facts'] if fact['name'] == 'maximum_supply_voltage')
    maximum['value'] = '40 V'
    maximum['basis'] = 'absolute_maximum'
    result = check_completeness(case, candidate, 'operational amplifier')
    assert 'wrong basis' in result['issues']['maximum_supply_voltage']
    assert 'wrong or unreviewed value' in result['issues']['maximum_supply_voltage']


def test_opamp_high_temperature_bias_bound_requires_characterization_qualifier():
    case, candidate = fixture('opamp_b_operating_and_stress_limits')
    bias = next(fact for fact in candidate['facts'] if fact['name'] == 'input_bias_current')
    bias['value'] = '50 nA'
    bias['conditions']['ambient_temperature'] = '-40 to 85 °C'
    result = check_completeness(case, candidate, 'operational amplifier')
    assert 'value and qualifier pairing mismatch' in result['issues']['input_bias_current']
    bias['conditions']['qualification'] = 'specified by characterization only'
    assert 'input_bias_current' not in check_completeness(case, candidate, 'operational amplifier')['issues']


def test_saved_link_is_scored_against_shared_b_ratings_without_hiding_missing_facts():
    _, candidate = fixture('opamp_b_operating_and_stress_limits')
    candidate['facts'] = [fact for fact in candidate['facts'] if fact['name'] != 'quiescent_current']
    result, = assess_report({'results': [{'case_id': 'opamp_saved_link_missing_suffix',
                                         'outcome': 'proposal', 'candidate': candidate}]})
    assert result['expectation_case_id'] == 'opamp_b_operating_and_stress_limits'
    assert result['source_hash_matches_review']
    assert result['issues']['quiescent_current'] == ['missing fact']
    assert result['value_coverage'] == .875
    candidate['source']['sha256'] = 'different'
    drifted, = assess_report({'results': [{'case_id': 'opamp_saved_link_missing_suffix',
                                          'outcome': 'proposal', 'candidate': candidate}]})
    assert drifted['value_coverage'] is None


def test_reviewed_supply_wording_preserves_endpoints_and_rail_convention():
    case, candidate = fixture('opamp_b_operating_and_stress_limits')
    for fact in candidate['facts']:
        if 'supply_convention' in fact['conditions']:
            fact['conditions']['supply_convention'] = 'VS= ([V+] – [V–])'
        elif fact['name'] != 'quiescent_current':
            fact['conditions']['supply_voltage'] = '5 V to 36 V (±2.5 V to ±18 V)'
    assert check_completeness(case, candidate, 'operational amplifier')['qualifier_coverage'] == 1
    candidate['facts'][3]['conditions']['supply_voltage'] = '5 V to 36 V (±5 V to ±36 V)'
    assert 'missing or unreviewed qualifier: supply_voltage' in check_completeness(
        case, candidate, 'operational amplifier')['issues']['input_offset_voltage']


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
