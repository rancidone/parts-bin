"""Extensible, category-specific facts and requirements over reviewed evidence."""

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .errors import DomainError, ErrorCode
from .specification_conditions import same_conditions


@dataclass(frozen=True)
class SpecificationDefinition:
    unit: str | None
    basis: str
    choices: tuple[str, ...] = ()


# Initial supported fields, not an inventory taxonomy. Unknown categories remain valid.
SPECIFICATIONS = {
    'resistor': {
        'resistance': SpecificationDefinition('Ω', 'nominal'),
        'tolerance': SpecificationDefinition('%', 'maximum'),
        'rated_power': SpecificationDefinition('W', 'rated'),
    },
    'capacitor': {
        'capacitance': SpecificationDefinition('F', 'nominal'),
        'tolerance': SpecificationDefinition('%', 'maximum'),
        'rated_voltage': SpecificationDefinition('V', 'rated'),
        'dielectric': SpecificationDefinition(None, 'nominal'),
    },
    'bjt': {
        'polarity': SpecificationDefinition(None, 'nominal', ('NPN', 'PNP')),
        'collector_emitter_voltage': SpecificationDefinition('V', 'absolute_maximum'),
        'continuous_collector_current': SpecificationDefinition('A', 'absolute_maximum_continuous'),
        'pulsed_collector_current': SpecificationDefinition('A', 'absolute_maximum_pulsed'),
        'dc_current_gain': SpecificationDefinition('ratio', 'minimum'),
    },
    'mosfet': {
        'channel_type': SpecificationDefinition(None, 'nominal', ('N-channel', 'P-channel')),
        'drain_source_voltage': SpecificationDefinition('V', 'absolute_maximum'),
        'continuous_drain_current': SpecificationDefinition('A', 'absolute_maximum_continuous'),
        'pulsed_drain_current': SpecificationDefinition('A', 'absolute_maximum_pulsed'),
        'on_resistance': SpecificationDefinition('Ω', 'maximum'),
        'gate_threshold_voltage': SpecificationDefinition('V', 'threshold'),
    },
    'inductor': {
        'inductance': SpecificationDefinition('H', 'nominal'),
        'tolerance': SpecificationDefinition('%', 'maximum'),
        'rated_current': SpecificationDefinition('A', 'rated'),
        'saturation_current': SpecificationDefinition('A', 'saturation'),
        'dc_resistance': SpecificationDefinition('Ω', 'maximum'),
    },
    'transformer': {
        'turns_ratio': SpecificationDefinition('ratio', 'nominal'),
        'primary_rated_voltage': SpecificationDefinition('V', 'rated'),
        'secondary_rated_voltage': SpecificationDefinition('V', 'rated'),
        'rated_apparent_power': SpecificationDefinition('VA', 'rated'),
        'minimum_frequency': SpecificationDefinition('Hz', 'operating_minimum'),
        'maximum_frequency': SpecificationDefinition('Hz', 'operating_maximum'),
    },
    'switch': {
        'contact_form': SpecificationDefinition(None, 'nominal'),
        'rated_voltage': SpecificationDefinition('V', 'rated'),
        'rated_current': SpecificationDefinition('A', 'rated'),
    },
}

# SI prefixes are case-sensitive. Ratio and percent have no prefixes.
_PREFIXES = {'': 0, 'p': -12, 'n': -9, 'u': -6, 'µ': -6, 'μ': -6, 'm': -3, 'k': 3, 'M': 6, 'G': 9}
MAX_CONDITIONS = 12
# Minimum qualification for confirming sourced capacitor facts. More conditions
# may govern a particular document; these checks cannot certify source completeness.
MINIMUM_SOURCE_CONDITIONS = {
    'capacitor': {
        'capacitance': (('measurement_frequency',), ('measurement_temperature',)),
        'rated_voltage': (('current_type', 'voltage_type'), ('rating_temperature',)),
    },
}


def missing_qualifiers(category: str, fact: dict) -> list[str]:
    groups = MINIMUM_SOURCE_CONDITIONS.get(category.lower(), {}).get(fact['name'], ())
    return [group[0] for group in groups if not any(key in fact['conditions'] for key in group)]



def invalid(message: str) -> None:
    raise DomainError(ErrorCode.INVALID_INPUT, message)


def definition(category: str, name: str) -> SpecificationDefinition:
    item = SPECIFICATIONS.get(category.lower(), {}).get(name)
    if item is None:
        invalid(f'Unsupported specification {name!r} for category {category!r}')
    return item


def contract(category: str) -> dict:
    fields = SPECIFICATIONS.get(category.lower(), {})
    return {'category': category, 'supported': bool(fields),
            'fields': {name: asdict(item) for name, item in fields.items()},
            'comparisons': ['eq', 'gte', 'lte'],
            'minimum_source_conditions': MINIMUM_SOURCE_CONDITIONS.get(category.lower(), {}),
            'conditions_policy': 'Match complete condition mappings. Explicit Celsius/humidity intervals and center ± tolerance compare by equal endpoints; omitted conditions are incomplete. Preserve original conditions and evidence.',
            'evidence_policy': 'Only accepted source evidence confirms requirements. User assertions remain incomplete.'}


def numeric_value(raw: str, unit: str) -> Decimal:
    if not isinstance(raw, str) or len(raw) > 100:
        invalid('Numeric specifications require a bounded decimal string with units')
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*([^\s]+)\s*', raw)
    if match is None:
        invalid(f'Use an explicit decimal value and {unit} unit')
    number, symbol = match.groups()
    aliases = {'ohm': 'Ω', 'ohms': 'Ω', 'Ω': 'Ω'}
    symbol = aliases.get(symbol, symbol)
    for label in ('ohms', 'ohm'):
        if symbol.endswith(label):
            symbol = symbol[:-len(label)] + 'Ω'
            break
    if unit in {'%', 'ratio'}:
        if symbol != unit:
            invalid(f'Expected {unit} unit')
        exponent = 0
    else:
        if not symbol.endswith(unit) or symbol[:-len(unit)] not in _PREFIXES:
            invalid(f'Expected {unit} with a supported SI prefix')
        exponent = _PREFIXES[symbol[:-len(unit)]]
    try:
        value = Decimal(number).as_tuple()
        return Decimal((value.sign, value.digits, value.exponent + exponent))
    except InvalidOperation:
        invalid('Invalid numeric specification')


def conditions(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or len(raw) > MAX_CONDITIONS:
        invalid('Conditions must be a bounded mapping of explicit names to strings')
    if any(not isinstance(key, str) or not key.strip() or len(key) > 80 or
           not isinstance(value, str) or not value.strip() or len(value) > 200
           for key, value in raw.items()):
        invalid('Condition names and values must be nonempty bounded strings')
    return dict(raw)


def _value(category: str, name: str, raw: Any, basis: str) -> str:
    item = definition(category, name)
    if basis != item.basis:
        invalid(f'{name} requires basis {item.basis}; qualifiers cannot be substituted')
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 100:
        invalid('Specification values must be nonempty bounded strings')
    if item.unit:
        return format(numeric_value(raw, item.unit), 'f')
    if item.choices and raw not in item.choices:
        invalid(f'{name} must be one of {item.choices}')
    return raw


@dataclass(frozen=True)
class SpecificationFact:
    name: str
    value: str
    basis: str
    conditions: Mapping[str, str]
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class SpecificationRequirement:
    name: str
    comparison: str
    value: str
    basis: str
    conditions: Mapping[str, str]


def validate_facts(category: str, raw: list[dict], *, part_number: str | None) -> list[dict]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
        invalid('Propose between one and twenty specification facts')
    facts, names = [], set()
    for record in raw:
        if not isinstance(record, dict) or set(record) != {'name', 'value', 'basis', 'conditions', 'evidence'}:
            invalid('Each fact requires name, value, basis, conditions, and evidence')
        name = record['name']
        if not isinstance(name, str) or name in names:
            invalid('Specification names must be distinct strings')
        names.add(name)
        _value(category, name, record['value'], record['basis'])
        qualified = conditions(record['conditions'])
        evidence = record['evidence']
        if not isinstance(evidence, dict) or evidence.get('kind') not in {'source', 'user_assertion'}:
            invalid('Evidence must distinguish a source from a user assertion')
        excerpt = evidence.get('excerpt')
        if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 1200:
            invalid('Every fact requires a bounded supporting passage')
        if evidence['kind'] == 'source':
            required = {'kind', 'excerpt', 'url', 'page', 'sha256', 'retrieved_at', 'part_number'}
            if set(evidence) not in (required, required | {'supporting_passages'}):
                invalid('Source evidence requires URL, page, hash, retrieval time, and exact ordering code')
            passages = evidence.get('supporting_passages', [])
            if not isinstance(passages, list) or len(passages) > 3:
                invalid('Source evidence supports at most three additional passages')
            for passage in passages:
                if (not isinstance(passage, dict) or set(passage) != {'page', 'excerpt'}
                        or type(passage['page']) is not int or passage['page'] < 1
                        or not isinstance(passage['excerpt'], str) or not passage['excerpt'].strip()
                        or len(passage['excerpt']) > 1200):
                    invalid('Supporting passages require a positive page and bounded source text')
            try:
                url = urlsplit(evidence['url']) if isinstance(evidence['url'], str) else None
            except ValueError:
                invalid('Invalid source URL')
            if url is None or len(evidence['url']) > 2048 or url.scheme != 'https' or not url.hostname or url.username or url.password:
                invalid('Source evidence requires an HTTPS URL without credentials')
            if type(evidence['page']) is not int or evidence['page'] < 1:
                invalid('Source page must be a positive integer')
            if not isinstance(evidence['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', evidence['sha256']):
                invalid('Source content requires a SHA-256 hash')
            from datetime import datetime
            try:
                timestamp = datetime.fromisoformat(evidence['retrieved_at'])
                if timestamp.tzinfo is None:
                    invalid('Retrieval time requires a timezone')
            except (ValueError, TypeError):
                invalid('Invalid source retrieval timestamp')
            if not part_number or evidence['part_number'] != part_number:
                invalid('Source facts require the inventory record’s exact ordering code')
        elif set(evidence) != {'kind', 'excerpt'}:
            invalid('User assertions carry their passage only, not invented source metadata')
        facts.append(asdict(SpecificationFact(name, record['value'], record['basis'], qualified, dict(evidence))))
    return facts


def validate_requirements(category: str, raw: list[dict]) -> list[dict]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
        invalid('Use between one and twenty specification requirements')
    requirements = []
    for record in raw:
        if not isinstance(record, dict) or set(record) != {'name', 'comparison', 'value', 'basis', 'conditions'}:
            invalid('Each requirement needs name, comparison, value, basis, and conditions')
        name, op = record['name'], record['comparison']
        if not isinstance(name, str) or not isinstance(op, str) or op not in {'eq', 'gte', 'lte'}:
            invalid('Unsupported specification comparison')
        value = _value(category, name, record['value'], record['basis'])
        if definition(category, name).unit is None and op != 'eq':
            invalid('Categorical specifications support equality only')
        requirements.append({**record, 'normalized_value': value, 'conditions': conditions(record['conditions'])})
    return requirements


def evaluate(category: str, facts: list[dict], requirements: list[dict]) -> tuple[bool, list[str], list[dict]]:
    by_name = {fact['name']: fact for fact in facts}
    missing, supporting = [], []
    for requirement in requirements:
        name = requirement['name']
        fact = by_name.get(name)
        if (fact is None or fact['evidence']['kind'] != 'source' or
                fact['basis'] != requirement['basis'] or missing_qualifiers(category, fact) or
                not same_conditions(fact['conditions'], requirement['conditions'])):
            missing.append(name)
            continue
        item = definition(category, name)
        left = _value(category, name, fact['value'], fact['basis'])
        right = requirement['normalized_value']
        if item.unit:
            left, right = Decimal(left), Decimal(right)
        op = requirement['comparison']
        if not {'eq': left == right, 'gte': left >= right, 'lte': left <= right}[op]:
            return False, [], []  # A known failing constraint excludes the candidate.
        supporting.append(fact)
    return True, missing, supporting
