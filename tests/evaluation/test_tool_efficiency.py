from evaluation.tool_efficiency import assess_tool_efficiency


def event(kind, **data):
    return {'kind': kind, 'data': data}


def call(name='search_parts'):
    return event('tool_call', name=name, arguments={'filters': {'value': 'private stock'}})


def test_same_turn_repeats_flagged_without_leaking_values():
    report = assess_tool_efficiency([event('user_message', text='private user'), call(), call(),
                                    event('tool_result', name='search_parts', result={'result': {'parts': ['private result']}})])
    assert report['tool_calls'] == 2
    assert len(report['repeated_reads_without_observed_change']) == 1
    assert report['tool_result_bytes'] > 0
    assert 'private' not in str(report)


def test_user_turn_mutation_approval_and_errors_reset_repeats():
    report = assess_tool_efficiency([call(), event('user_message'), call(), call('update_part'), call(),
                                    event('approval_decision'), call(),
                                    event('tool_result', name='search_parts', result={'error': {'code': 'invalid_input'}}), call()])
    assert report['repeated_reads_without_observed_change'] == []
    assert report['errors_by_tool'] == {'search_parts': 1}
