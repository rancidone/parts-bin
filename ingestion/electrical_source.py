"""Electrical extraction shapes and evidence checks for supplied PDF passages."""

from domain import DomainError
from domain.specifications import MAX_CONDITIONS, contract, validate_facts
from ingestion.errors import EnrichmentError


def facts_schema(category: str) -> dict:
    fields = contract(category)['fields']
    if not fields:
        raise EnrichmentError('This inventory category has no electrical extraction contract')
    condition = {'type': 'object', 'additionalProperties': False,
                 'properties': {'name': {'type': 'string'}, 'value': {'type': 'string'}},
                 'required': ['name', 'value']}
    properties = {
        'name': {'type': 'string', 'enum': list(fields)},
        'value': {'type': 'string'},
        'basis': {'type': 'string', 'enum': sorted({item['basis'] for item in fields.values()})},
        # Strict Responses schemas use fixed object keys; convert pairs only after validation.
        'conditions': {'type': 'array', 'items': condition, 'maxItems': MAX_CONDITIONS},
        'evidence': {'type': 'object', 'additionalProperties': False,
                     'properties': {'passage_ids': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                                                    'items': {'type': 'integer'}}},
                     'required': ['passage_ids']},
    }
    return {'type': 'array', 'maxItems': 20, 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': properties, 'required': list(properties)}}


def validate_source_facts(raw: list, category: str, part_number: str, document,
                          passages: list[dict]) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 20:
        raise EnrichmentError('Invalid electrical fact list')
    facts = []
    for record in raw:
        if not isinstance(record, dict) or set(record) != {'name', 'value', 'basis', 'conditions', 'evidence'}:
            raise EnrichmentError('Invalid electrical fact')
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
        facts.append({**record, 'conditions': conditions, 'evidence': {
            'kind': 'source', 'page': page, 'excerpt': excerpt, 'url': document.url,
            'sha256': document.sha256, 'retrieved_at': document.retrieved_at,
            'part_number': part_number,
            **({'supporting_passages': cited[1:]} if len(cited) > 1 else {})}})
    try:
        return validate_facts(category, facts, part_number=part_number) if facts else []
    except DomainError as exc:
        raise EnrichmentError(exc.message) from exc
