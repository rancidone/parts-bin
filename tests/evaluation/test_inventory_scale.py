import pytest

from evaluation.inventory_scale import measure


async def test_scale_measurements_distinguish_bounded_results_from_candidate_reads():
    report = await measure(8, repeats=1, limit=1)
    measurements = {item['workload']: item['samples'][0] for item in report['measurements']}
    exact = measurements['exact_identity']
    assert exact['reads'] == {'inventory_search_calls': 1, 'candidate_rows_materialized': 1}
    assert exact['returned_part_records'] == 1
    electrical = measurements['electrical_first_page']
    assert electrical['reads'] == {'inventory_search_calls': 1, 'candidate_rows_materialized': 8,
                                   'specification_batch_calls': 1, 'specification_part_ids_requested': 8}
    assert electrical['returned_part_records'] == 2
    assert electrical['counts'] == {'match_count': 2, 'incomplete_count': 4, 'next_offset': 1}
    assert measurements['electrical_later_page']['reads'] == electrical['reads']
    assert measurements['exact_bom']['returned_part_records'] == 8
    assert measurements['overlapping_bom']['returned_part_records'] == 2
    assert measurements['overlapping_bom']['bom_candidate_matches'] == 5
    assert measurements['overlapping_bom']['overlapping_requirement_groups'] == 1
    assert all(item['response_bytes'] > 0 and item['elapsed_ms'] >= 0 for item in measurements.values())
    assert all(len(item['samples']) == 1 for item in report['measurements'])


@pytest.mark.parametrize('arguments', [{'size': 0}, {'size': 100001}, {'size': 1, 'repeats': 0}, {'size': 1, 'limit': 101}])
async def test_scale_rejects_unbounded_or_empty_runs(arguments):
    with pytest.raises(ValueError):
        await measure(**arguments)
