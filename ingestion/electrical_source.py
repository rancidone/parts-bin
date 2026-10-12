"""Electrical extraction shapes and evidence checks for supplied PDF passages."""

from domain import DomainError
from domain.specifications import MAX_CONDITIONS, contract, definition, validate_facts
from ingestion.errors import EnrichmentError


def facts_schema(category: str, *, passage_ids: list[int] | None = None) -> dict:
    fields = contract(category)['fields']
    if not fields:
        raise EnrichmentError('This inventory category has no electrical extraction contract')
    condition = {'type': 'object', 'additionalProperties': False,
                 'properties': {'name': {'type': 'string'}, 'value': {'type': 'string'}},
                 'required': ['name', 'value']}
    properties = {
        'value': {'type': 'string'},
        # Strict Responses schemas use fixed object keys; convert pairs only after validation.
        'conditions': {'type': 'array', 'items': condition, 'maxItems': MAX_CONDITIONS},
        'evidence': {'type': 'object', 'additionalProperties': False,
                     'properties': {'passage_ids': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                                                    'items': {'type': 'integer', **({'enum': passage_ids} if passage_ids else {})}}},
                     'required': ['passage_ids']},
    }
    return {'type': 'object', 'additionalProperties': False, 'required': list(fields),
            'properties': {name: {'type': ['object', 'null'], 'additionalProperties': False,
                'properties': {**properties, 'basis': {'type': 'string', 'enum': [item['basis']]}},
                'required': [*properties, 'basis']} for name, item in fields.items()}}


def source_fact_records(raw: dict, category: str) -> list[dict]:
    """Require an explicit decision for every field; null remains unsupported."""
    names = contract(category)['fields']
    if not isinstance(raw, dict) or set(raw) != set(names):
        raise EnrichmentError('Electrical extraction must consider every supported field')
    records = []
    for name, value in raw.items():
        if value is None:
            continue
        if not isinstance(value, dict) or set(value) != {'value', 'basis', 'conditions', 'evidence'}:
            raise EnrichmentError('Invalid electrical fact')
        records.append({'name': name, **value})
    return records


def resolve_source_metadata(raw: dict, passages: list[dict]) -> dict:
    """Resolve model-selected identity citations to actual supplied source text."""
    def resolve(evidence):
        if not isinstance(evidence, dict) or set(evidence) != {'passage_id'}:
            raise EnrichmentError('Identity evidence must cite a supplied passage ID')
        index = evidence['passage_id']
        if type(index) is not int or not 1 <= index <= len(passages):
            raise EnrichmentError('Identity evidence must cite a supplied passage ID')
        passage = passages[index - 1]
        return {'page': passage['page'], 'excerpt': passage['text']}

    raw = dict(raw)
    fields = raw.get('fields')
    if not isinstance(fields, dict):
        raise EnrichmentError('Invalid extraction fields')
    raw['fields'] = {name: {**item, 'evidence': resolve(item.get('evidence'))}
                     if isinstance(item, dict) else item for name, item in fields.items()}
    if raw.get('mismatch_evidence') is not None:
        raw['mismatch_evidence'] = resolve(raw['mismatch_evidence'])
    return raw


def validate_source_facts(raw: list, category: str, part_number: str, document,
                          passages: list[dict], *, rejected: list[dict] | None = None) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 20:
        raise EnrichmentError('Invalid electrical fact list')
    facts, names = [], set()
    for record in raw:
        if not isinstance(record, dict) or set(record) != {'name', 'value', 'basis', 'conditions', 'evidence'}:
            raise EnrichmentError('Invalid electrical fact')
        name = record['name']
        if not isinstance(name, str) or name in names:
            raise EnrichmentError('Specification names must be distinct strings')
        names.add(name)
        try:
            item = definition(category, name)
            if record['basis'] != item.basis:
                raise EnrichmentError(f'{name} requires basis {item.basis}; qualifiers cannot be substituted')
        except DomainError as exc:
            raise EnrichmentError(exc.message) from exc
        pairs = record['conditions']
        if not isinstance(pairs, list) or len(pairs) > MAX_CONDITIONS:
            raise EnrichmentError('Invalid electrical conditions')
        conditions = {}
        for pair in pairs:
            if (not isinstance(pair, dict) or set(pair) != {'name', 'value'}
                    or not isinstance(pair['name'], str) or pair['name'] in conditions):
                raise EnrichmentError('Condition names must be distinct strings')
            conditions[pair['name']] = pair['value']
        evidence = record['evidence']
        if not isinstance(evidence, dict) or set(evidence) != {'passage_ids'}:
            raise EnrichmentError('Invalid electrical evidence')
        ids = evidence['passage_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 4
                or any(type(index) is not int or not 1 <= index <= len(passages) for index in ids)
                or len(set(ids)) != len(ids)):
            raise EnrichmentError('Electrical evidence must cite distinct supplied passage IDs')
        cited = [{'page': passages[index - 1]['page'], 'excerpt': passages[index - 1]['text']} for index in ids]
        for passage in cited:
            if (not 1 <= passage['page'] <= len(document.pages) or len(passage['excerpt']) > 1200
                    or passage['excerpt'] not in document.pages[passage['page'] - 1]):
                raise EnrichmentError('Supplied passage is not present in the source')
        page, excerpt = cited[0]['page'], cited[0]['excerpt']
        proposed = {**record, 'conditions': conditions, 'evidence': {
            'kind': 'source', 'page': page, 'excerpt': excerpt, 'url': document.url,
            'sha256': document.sha256, 'retrieved_at': document.retrieved_at,
            'part_number': part_number,
            **({'supporting_passages': cited[1:]} if len(cited) > 1 else {})}}
        try:
            facts.extend(validate_facts(category, [proposed], part_number=part_number))
        except DomainError as exc:
            if rejected is None:
                raise EnrichmentError(exc.message) from exc
            rejected.append({'name': name, 'reason': exc.message})
    return facts
