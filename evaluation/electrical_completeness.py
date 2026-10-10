"""Compare candidates with independently reviewed source expectations, not themselves."""

import argparse
import json
from pathlib import Path

from domain import DomainError
from domain.specification_conditions import interval
from domain.specifications import definition, numeric_value

EXPECTATIONS = Path(__file__).parent / 'enrichment' / 'electrical_expectations.json'


def _equal(raw, expected, unit=None):
    if not isinstance(raw, str):
        return False
    if unit == '°C':
        left, right = interval(raw, unit), interval(expected, unit)
        return left is not None and left == right
    if unit:
        try:
            return numeric_value(raw, unit) == numeric_value(expected, unit)
        except DomainError:
            return False
    return ' '.join(raw.casefold().split()).rstrip('.') == ' '.join(expected.casefold().split()).rstrip('.')


def check_completeness(case, candidate, category):
    """Flags source/value/qualifier gaps; full quotation interpretation remains human."""
    expected = case['facts']
    facts = {fact['name']: fact for fact in (candidate or {}).get('facts', [])}
    values, qualified, issues = [], [], {}
    source_matches = (candidate or {}).get('source', {}).get('sha256') == case['source']['sha256']
    for name, required in expected.items():
        fact = facts.get(name)
        gaps = []
        if fact is None:
            issues[name] = ['missing fact']
            continue
        unit = definition(category, name).unit
        if fact['basis'] != required['basis']:
            gaps.append('wrong basis')
        if not any(_equal(fact['value'], value, unit) for value in required['values']):
            gaps.append('wrong or unreviewed value')
        if not gaps:
            values.append(name)
        for qualifier in required['conditions']:
            if not any(key in fact['conditions'] and any(
                _equal(fact['conditions'][key], value, qualifier.get('unit'))
                for value in qualifier['values']) for key in qualifier['names']):
                gaps.append('missing or unreviewed qualifier: ' + qualifier['names'][0])
        if required.get('paired_conditions') and not any(
                _equal(fact['value'], pair['value'], unit) and all(
                    _equal(fact['conditions'].get(key), value, next(
                        (q.get('unit') for q in required['conditions'] if key in q['names']), None))
                    for key, value in pair['conditions'].items())
                for pair in required['paired_conditions']):
            gaps.append('value and qualifier pairing mismatch')
        if gaps:
            issues[name] = gaps
        else:
            qualified.append(name)
    unsupported = [name for name in case['unsupported_fact_names'] if name in facts]
    return {'status': 'needs_review', 'source_hash_matches_review': source_matches,
            'expected_fields': list(expected), 'value_matches': values, 'qualified_matches': qualified,
            'value_coverage': round(len(values) / len(expected), 3) if source_matches else None,
            'qualifier_coverage': round(len(qualified) / len(expected), 3) if source_matches else None,
            'issues': issues, 'unsupported_proposals': unsupported,
            'note': 'Computed against independent expectations. Pairing, bound direction, RMS meaning, '
                    'and quotation-to-claim association require visual review. Source drift invalidates expectations.'}


def assess_report(report):
    expectations = {case['id']: case for case in json.loads(EXPECTATIONS.read_text())['cases']}
    from .live_electrical import load_cases
    sources = {case['id']: case for case in load_cases()}
    return [{'case_id': row['case_id'], 'extraction_outcome': row['outcome'],
             'expectation_case_id': expectation_id,
             'reported_coverage': (row.get('candidate') or {}).get('extraction_assessment', {}).get('confidence_score'),
             **check_completeness(expectations[expectation_id], row.get('candidate'), sources[row['case_id']]['category'])}
            for row in report['results']
            if row['case_id'] in sources
            and (expectation_id := sources[row['case_id']].get('expectation_case_id', row['case_id'])) in expectations]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps({'report': str(args.report), 'results': assess_report(
        json.loads(args.report.read_text()))}, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'output': str(args.output), 'status': 'needs_review'}))
    raise SystemExit(2)


if __name__ == '__main__':
    main()
