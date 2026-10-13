"""Extensible, category-specific facts and requirements over reviewed evidence."""

from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .errors import DomainError, ErrorCode
from .specification_conditions import conditions_cover, same_conditions
from .quantities import compare_intervals, interval, relative_interval_gap
from .search import search_category


@dataclass(frozen=True)
class SpecificationDefinition:
    unit: str | None
    basis: str
    choices: tuple[str, ...] = ()


# Initial supported fields, not an inventory taxonomy. Unknown categories remain valid.
SPECIFICATIONS = {
    'linear regulator': {
        'minimum_input_voltage': SpecificationDefinition('V', 'operating_minimum'),
        'maximum_input_voltage': SpecificationDefinition('V', 'operating_maximum'),
        'absolute_maximum_input_voltage': SpecificationDefinition('V', 'absolute_maximum'),
        'output_mode': SpecificationDefinition(None, 'nominal', ('fixed', 'adjustable')),
        'output_voltage': SpecificationDefinition('V', 'nominal'),
        'rated_output_current': SpecificationDefinition('A', 'rated'),
        'dropout_voltage': SpecificationDefinition('V', 'maximum'),
        'quiescent_current': SpecificationDefinition('A', 'typical'),
    },
    'buck converter': {
        'minimum_input_voltage': SpecificationDefinition('V', 'operating_minimum'),
        'maximum_input_voltage': SpecificationDefinition('V', 'operating_maximum'),
        'absolute_maximum_input_voltage': SpecificationDefinition('V', 'absolute_maximum'),
        'output_mode': SpecificationDefinition(None, 'nominal', ('fixed', 'adjustable')),
        'minimum_output_voltage': SpecificationDefinition('V', 'operating_minimum'),
        'maximum_output_voltage': SpecificationDefinition('V', 'operating_maximum'),
        'rated_output_current': SpecificationDefinition('A', 'rated'),
        'switching_frequency': SpecificationDefinition('Hz', 'typical'),
        'quiescent_current': SpecificationDefinition('A', 'typical'),
    },
    'led': {
        'color': SpecificationDefinition(None, 'nominal'),
        'forward_voltage': SpecificationDefinition('V', 'maximum'),
        'continuous_forward_current': SpecificationDefinition('A', 'absolute_maximum_continuous'),
        'peak_forward_current': SpecificationDefinition('A', 'absolute_maximum_pulsed'),
        'minimum_dominant_wavelength': SpecificationDefinition('m', 'minimum'),
        'maximum_dominant_wavelength': SpecificationDefinition('m', 'maximum'),
        'luminous_intensity': SpecificationDefinition('cd', 'minimum'),
    },
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
    'diode': {
        'reverse_voltage': SpecificationDefinition('V', 'absolute_maximum_continuous'),
        'repetitive_peak_reverse_voltage': SpecificationDefinition('V', 'absolute_maximum_repetitive_peak'),
        'continuous_forward_current': SpecificationDefinition('A', 'absolute_maximum_continuous'),
        'surge_forward_current': SpecificationDefinition('A', 'absolute_maximum_nonrepetitive_peak'),
        'forward_voltage': SpecificationDefinition('V', 'maximum'),
        'reverse_leakage_current': SpecificationDefinition('A', 'maximum'),
        'junction_capacitance': SpecificationDefinition('F', 'maximum'),
        'reverse_recovery_time': SpecificationDefinition('s', 'maximum'),
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
    'operational amplifier': {
        'minimum_supply_voltage': SpecificationDefinition('V', 'operating_minimum'),
        'maximum_supply_voltage': SpecificationDefinition('V', 'operating_maximum'),
        'absolute_maximum_supply_voltage': SpecificationDefinition('V', 'absolute_maximum'),
        'input_offset_voltage': SpecificationDefinition('V', 'maximum_magnitude'),
        'input_bias_current': SpecificationDefinition('A', 'maximum_magnitude'),
        'gain_bandwidth_product': SpecificationDefinition('Hz', 'typical'),
        'slew_rate': SpecificationDefinition('V/s', 'typical'),
        'quiescent_current': SpecificationDefinition('A', 'typical'),
    },
}

MAX_CONDITIONS = 12
OPERATING_CONDITION_BASES = frozenset({'operating_minimum', 'operating_maximum'})
# Minimum qualification for confirming sourced electrical facts. More conditions
# may govern a particular document; these checks cannot certify source completeness.
MINIMUM_SOURCE_CONDITIONS = {
    'linear regulator': {
        'minimum_input_voltage': (('ambient_temperature', 'junction_temperature'),),
        'maximum_input_voltage': (('ambient_temperature', 'junction_temperature'),),
        'absolute_maximum_input_voltage': (('ambient_temperature', 'junction_temperature'),),
        'output_voltage': (('input_voltage',), ('output_current',), ('ambient_temperature', 'junction_temperature')),
        'rated_output_current': (('ambient_temperature', 'junction_temperature'),),
        'dropout_voltage': (('output_current',), ('output_voltage',), ('ambient_temperature', 'junction_temperature')),
        'quiescent_current': (('input_voltage',), ('output_current',), ('ambient_temperature', 'junction_temperature')),
    },
    'buck converter': {
        'minimum_input_voltage': (('ambient_temperature', 'junction_temperature'),),
        'maximum_input_voltage': (('ambient_temperature', 'junction_temperature'),),
        'absolute_maximum_input_voltage': (('ambient_temperature', 'junction_temperature'),),
        'minimum_output_voltage': (('input_voltage',), ('ambient_temperature', 'junction_temperature')),
        'maximum_output_voltage': (('input_voltage',), ('ambient_temperature', 'junction_temperature')),
        'rated_output_current': (('ambient_temperature', 'junction_temperature'),),
        'switching_frequency': (('input_voltage',), ('ambient_temperature', 'junction_temperature')),
        'quiescent_current': (('input_voltage',), ('output_current',), ('operating_mode',), ('ambient_temperature', 'junction_temperature')),
    },
    'led': {
        'forward_voltage': (('forward_current',), ('ambient_temperature', 'junction_temperature')),
        'continuous_forward_current': (('ambient_temperature', 'junction_temperature'),),
        'peak_forward_current': (('pulse_duration',), ('duty_cycle',), ('ambient_temperature', 'junction_temperature')),
        'minimum_dominant_wavelength': (('forward_current',), ('ambient_temperature', 'junction_temperature')),
        'maximum_dominant_wavelength': (('forward_current',), ('ambient_temperature', 'junction_temperature')),
        'luminous_intensity': (('forward_current',), ('ambient_temperature', 'junction_temperature')),
    },
    'capacitor': {
        'capacitance': (('measurement_frequency',), ('measurement_temperature',)),
        'rated_voltage': (('current_type', 'voltage_type'), ('rating_temperature',)),
    },
    'bjt': {
        'dc_current_gain': (('collector_current',), ('collector_emitter_voltage',),
                            ('ambient_temperature', 'junction_temperature')),
        'continuous_collector_current': (('ambient_temperature', 'case_temperature', 'junction_temperature'),),
        'pulsed_collector_current': (('pulse_duration',),
                                   ('ambient_temperature', 'case_temperature', 'junction_temperature')),
    },
    'mosfet': {
        'on_resistance': (('gate_source_voltage',), ('drain_current',), ('junction_temperature',)),
        'gate_threshold_voltage': (('drain_current',), ('drain_source_voltage',),
                                   ('junction_temperature',), ('value_kind',)),
        'continuous_drain_current': (('gate_source_voltage',),
                                     ('ambient_temperature', 'case_temperature', 'solder_point_temperature', 'junction_temperature')),
        'pulsed_drain_current': (('pulse_duration',),
                                ('ambient_temperature', 'case_temperature', 'solder_point_temperature', 'junction_temperature')),
    },
    'diode': {
        'continuous_forward_current': (('ambient_temperature', 'case_temperature', 'lead_temperature', 'solder_point_temperature'),),
        'surge_forward_current': (('pulse_duration',), ('waveform',), ('junction_temperature',)),
        'forward_voltage': (('forward_current',), ('junction_temperature',)),
        'reverse_leakage_current': (('reverse_voltage',), ('junction_temperature',)),
        'junction_capacitance': (('reverse_voltage',), ('measurement_frequency',), ('junction_temperature',)),
        'reverse_recovery_time': (('forward_current',), ('reverse_current',),
                                  ('recovery_endpoint_current',), ('load_resistance',), ('junction_temperature',)),
    },
    'operational amplifier': {
        'minimum_supply_voltage': (('supply_convention',), ('ambient_temperature',)),
        'maximum_supply_voltage': (('supply_convention',), ('ambient_temperature',)),
        'absolute_maximum_supply_voltage': (('supply_convention',), ('ambient_temperature',)),
        'input_offset_voltage': (('supply_voltage',), ('ambient_temperature',), ('common_mode_voltage',)),
        'input_bias_current': (('supply_voltage',), ('ambient_temperature',), ('common_mode_voltage',)),
        'gain_bandwidth_product': (('supply_voltage',), ('ambient_temperature',)),
        'slew_rate': (('supply_voltage',), ('ambient_temperature',), ('closed_loop_gain',)),
        'quiescent_current': (('supply_voltage',), ('ambient_temperature',), ('current_scope',)),
    },
}


def missing_qualifiers(category: str, fact: dict) -> list[str]:
    groups = MINIMUM_SOURCE_CONDITIONS.get(search_category(category), {}).get(fact['name'], ())
    return [group[0] for group in groups if not any(key in fact['conditions'] for key in group)]



def invalid(message: str) -> None:
    raise DomainError(ErrorCode.INVALID_INPUT, message)


def definition(category: str, name: str) -> SpecificationDefinition:
    item = SPECIFICATIONS.get(search_category(category), {}).get(name)
    if item is None:
        invalid(f'Unsupported specification {name!r} for category {category!r}')
    return item


def contract(category: str) -> dict:
    fields = SPECIFICATIONS.get(search_category(category), {})
    return {'category': category, 'supported': bool(fields),
            'fields': {name: asdict(item) for name, item in fields.items()},
            'comparisons': ['eq', 'gte', 'lte'],
            'operating_condition_coverage_bases': sorted(OPERATING_CONDITION_BASES),
            'minimum_source_conditions': MINIMUM_SOURCE_CONDITIONS.get(search_category(category), {}),
            **({'value_policy': 'Supply values are total positive-to-negative rail voltages. Check both operating endpoints to establish supply suitability; an absolute maximum is a stress rating. Offset voltage and bias current are worst-case magnitudes, retaining current direction where sourced. Typical bandwidth, slew rate and quiescent current are not guarantees. Preserve per-amplifier versus whole-device current scope and all applicable table conditions.'}
               if search_category(category) == 'operational amplifier' else {}),
            **({'value_policy': 'Input operating endpoints must both cover the requested supply. Absolute maxima are stress limits. Fixed output voltage belongs to the exact voltage variant; an adjustable reference or example circuit is not a fixed output. Preserve input/output voltage, load, temperature, thermal and capacitor conditions. Rated load current is distinct from switch current limit; dropout is not input operating minimum. Typical current and switching frequency are not guarantees.'}
               if search_category(category) in {'linear regulator', 'buck converter'} else {}),
            **({'value_policy': 'Preserve exact color and intensity bin identity. Maximum forward voltage requires its test current and temperature. Continuous and pulsed stress currents are distinct; retain duty cycle and pulse duration. Dominant wavelength is distinct from peak wavelength. Minimum luminous intensity is distinct from typical intensity and luminous flux; retain test current, temperature and measurement geometry. Unmarked LEDs require identity clarification.'}
               if search_category(category) == 'led' else {}),
            'numeric_value_policy': 'Values accept nonnegative scalars, ordered ranges and center ± tolerance with units. Equality requires equal endpoints. At least compares the stored lower bound to the requested upper bound; at most compares the stored upper bound to the requested lower bound. Overlapping but unconfirmed comparisons remain incomplete. Nominal passive identity facts remain scalar. For operating-range coverage, require the operating minimum at most the requested range and operating maximum at least the requested range under complete source-covered operating conditions. Never substitute stress limits or infer derating.',
            'conditions_policy': 'Match complete condition mappings. Named electrical, Celsius and percentage conditions compare scalars, explicit ranges and center ± tolerance by equal unit-aware endpoints. Operating minimum/maximum facts also cover contained requested condition intervals. Other bases require equal conditions. Omitted or extra conditions are incomplete; numeric containment cannot turn a test point, typical value or stress rating into an operating guarantee. Unknown conditions and additional qualifiers remain literal. Preserve original conditions and evidence.',
            'partial_match_policy': 'Known source-backed value failures are partial matches, with each requested requirement and actual fact retained. Nearby passive nominal values are also partial matches when a category and value filter are explicit. Rank by most confirmed requirements, fewer unresolved qualifiers, fewer mismatches, then normalized numeric shortfall. Unknown or mismatched conditions remain unconfirmed. Partial matches do not meet all requirements; explain every difference and require the user to choose which requirements to relax.',
            'evidence_policy': 'Only accepted source evidence confirms requirements. User assertions remain incomplete.'}


def conditions(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or len(raw) > MAX_CONDITIONS:
        invalid('Conditions must be a bounded mapping of explicit names to strings')
    if any(not isinstance(key, str) or not key.strip() or len(key) > 80 or
           not isinstance(value, str) or not value.strip() or len(value) > 200
           for key, value in raw.items()):
        invalid('Condition names and values must be nonempty bounded strings')
    return dict(raw)


def _value(category: str, name: str, raw: Any, basis: str) -> str | tuple[str, str]:
    item = definition(category, name)
    if basis != item.basis:
        invalid(f'{name} requires basis {item.basis}; qualifiers cannot be substituted')
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 100:
        invalid('Specification values must be nonempty bounded strings')
    if item.unit:
        endpoints = interval(raw, item.unit)
        if endpoints is None or endpoints[0] < 0:
            invalid('Use a nonnegative scalar or ordered interval with compatible explicit units')
        return tuple(format(endpoint, 'f') for endpoint in endpoints)
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
        value = _value(category, name, record['value'], record['basis'])
        if name in {'resistance', 'capacitance', 'inductance'} and value[0] != value[1]:
            invalid('Nominal passive identity requires a scalar; preserve tolerance separately')
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


@dataclass(frozen=True)
class SpecificationEvaluation:
    supporting_facts: list[dict]
    missing_or_unqualified: list[str]
    failed_requirements: list[dict]
    distance: Decimal = Decimal(0)


def evaluate(category: str, facts: list[dict], requirements: list[dict]) -> SpecificationEvaluation:
    by_name = {fact['name']: fact for fact in facts}
    missing, supporting, failed = [], [], []
    distance = Decimal(0)
    for requirement in requirements:
        name = requirement['name']
        match_conditions = conditions_cover if requirement['basis'] in OPERATING_CONDITION_BASES else same_conditions
        fact = by_name.get(name)
        if (fact is None or fact['evidence']['kind'] != 'source' or
                fact['basis'] != requirement['basis'] or missing_qualifiers(category, fact) or
                not match_conditions(fact['conditions'], requirement['conditions'])):
            missing.append(name)
            continue
        item = definition(category, name)
        left = _value(category, name, fact['value'], fact['basis'])
        right = requirement['normalized_value']
        if item.unit:
            left, right = tuple(map(Decimal, left)), tuple(map(Decimal, right))
            outcome = compare_intervals(left, right, requirement['comparison'])
        else:
            outcome = left == right
        if outcome is False:
            failed.append({'requirement': {key: requirement[key] for key in
                ('name', 'comparison', 'value', 'basis', 'conditions')}, 'fact': fact})
            gap = relative_interval_gap(left, right, requirement['comparison']) if item.unit else Decimal(1)
            with localcontext() as context:
                context.prec = 220
                distance += gap
            continue
        if outcome is None:
            missing.append(name)  # Overlap alone cannot confirm a whole-interval comparison.
            continue
        supporting.append(fact)
    return SpecificationEvaluation(supporting, missing, failed, distance)
