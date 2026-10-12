"""Render domain-shaped synthetic candidates as electrical model responses."""

import copy
import json


def model_response(candidate, request):
    body = json.loads(request.content)
    supplied = json.loads(body['input'])
    result = copy.deepcopy(candidate)
    fields = dict.fromkeys(supplied['specification_contract']['fields'])
    for fact in result.pop('facts'):
        fact = dict(fact)
        name = fact.pop('name')
        # Keep intentionally malformed duplicate fixtures invalid on the wire.
        fields[name if fields.get(name) is None else name + '_duplicate'] = fact
    result['facts'] = fields
    passages = supplied['pages']

    def citation(evidence):
        if set(evidence) != {'page', 'excerpt'}:
            return evidence
        matches = [p['passage_id'] for p in passages if p['page'] == evidence['page']
                   and ' '.join(evidence['excerpt'].split()) in ' '.join(p['text'].split())]
        return {'passage_id': matches[0] if matches else 0}

    for item in result['fields'].values():
        if isinstance(item, dict):
            item['evidence'] = citation(item['evidence'])
    if result['mismatch_evidence'] is not None:
        result['mismatch_evidence'] = citation(result['mismatch_evidence'])
    return json.dumps(result)
