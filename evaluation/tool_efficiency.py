"""Flag redundant calls for review without retaining arguments or conversations."""

from collections import Counter
import json

from agent_runtime.telemetry import fingerprint


def assess_tool_efficiency(events: list[dict]) -> dict:
    """Scope repeats to one user turn; repeated reads are flags, not proof of waste.

    A mutation, approval decision or tool error may justify a repeated read.
    Inventory can also change externally, so this report requires source review.
    """
    counts, errors = Counter(), Counter()
    seen, repeats = set(), []
    turn, result_bytes = 0, 0
    mutating = {'add_part', 'add_stock', 'update_part', 'bulk_update_parts', 'delete_part',
                'lookup_part_specs', 'ingest_datasheet', 'stage_specification_review',
                'apply_review', 'reject_review', 'apply_specification_review', 'reject_specification_review'}
    for event in events:
        kind, data = event['kind'], event.get('data', {})
        if kind == 'user_message':
            turn += 1
            seen.clear()
        elif kind == 'approval_decision':
            seen.clear()
        elif kind == 'tool_call':
            name = data['name']
            counts[name] += 1
            key = (name, fingerprint(data.get('arguments', {})))
            if name in mutating:
                seen.clear()
            else:
                if key in seen:
                    repeats.append({'turn': turn, 'sequence': event.get('sequence'), 'tool': name})
                seen.add(key)
        elif kind == 'tool_result':
            result = data.get('result', {})
            result_bytes += len(json.dumps(result, ensure_ascii=False).encode())
            if isinstance(result, dict) and isinstance(result.get('error'), dict):
                errors[data.get('name', 'unknown')] += 1
                seen.clear()
    return {'tool_calls': sum(counts.values()), 'calls_by_tool': dict(counts),
            'errors_by_tool': dict(errors), 'tool_result_bytes': result_bytes,
            'repeated_reads_without_observed_change': repeats,
            'semantic_review': 'needs_review'}
