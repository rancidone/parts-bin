"""Offline SQLite search measurements using disposable synthetic stock only."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from db.repository import SQLiteInventoryRepository, SQLitePartsBinRepository
from domain import AddPartRequest, PartFields, PartsBinService
from tools import PartsBinToolRegistry


class MeasuredInventory(SQLiteInventoryRepository):
    """Count adapter reads and materialized candidates, not estimated SQL work."""

    def __init__(self, database):
        super().__init__(database)
        self.reads = Counter()

    def search(self, filters):
        parts = super().search(filters)
        self.reads['inventory_search_calls'] += 1
        self.reads['candidate_rows_materialized'] += len(parts)
        return parts

    def specifications(self, part_id):
        self.reads['specification_read_calls'] += 1
        return super().specifications(part_id)

    def specifications_for_parts(self, part_ids):
        self.reads['specification_batch_calls'] += 1
        self.reads['specification_part_ids_requested'] += len(part_ids)
        return super().specifications_for_parts(part_ids)


def seed(repository: SQLitePartsBinRepository, size: int) -> None:
    """One-category stress case with confirmed, incomplete and failing evidence."""
    with repository.transaction() as unit:
        service = PartsBinService(unit)
        for index in range(size):
            part = service.add_part(AddPartRequest(PartFields(
                'resistor', 'passive', 10, value='10k' if index % 2 == 0 else '1k',
                part_number=f'SYNTHETIC-{index:06d}', package='0603',
                description='Synthetic scale-measurement resistor')))
            if index % 4 == 1:
                continue
            fact = {'name': 'rated_power', 'value': '0.125 W' if index % 4 == 3 else '0.5 W',
                    'basis': 'rated', 'conditions': {'ambient_temperature': '25 °C'},
                    'evidence': {'kind': 'source', 'url': 'https://example.com/synthetic.pdf',
                                 'page': 1, 'sha256': 'a' * 64,
                                 'retrieved_at': '2026-10-12T00:00:00Z',
                                 'part_number': part.part_number,
                                 'excerpt': 'Synthetic rating fixture; not manufacturer evidence.'}}
            service.stage_specifications(part, [fact])
            if index % 4 != 2:
                service.apply_specification_review(part.id)


def workloads(size: int, limit: int) -> list[tuple[str, str, dict]]:
    requirement = {'name': 'rated_power', 'comparison': 'gte', 'value': '0.25 W',
                   'basis': 'rated', 'conditions': {'ambient_temperature': '25 °C'}}
    return [
        ('exact_identity', 'search_parts', {'filters': {'part_number': 'SYNTHETIC-000000'}, 'limit': limit}),
        ('nominal_value', 'search_parts', {'filters': {'part_category': 'resistor', 'value': '10 kΩ'}, 'limit': limit}),
        ('broad_candidates', 'search_candidates', {'query': 'resistor', 'limit': limit}),
        ('narrow_candidates', 'search_candidates', {'query': 'SYNTHETIC-000000', 'limit': limit}),
        ('electrical_first_page', 'search_parts', {'filters': {'part_category': 'resistor'}, 'requirements': [requirement], 'limit': limit}),
        ('electrical_later_page', 'search_parts', {'filters': {'part_category': 'resistor'}, 'requirements': [requirement], 'limit': limit, 'offset': limit}),
        ('exact_bom', 'check_inventory', {'items': [{'filters': {'part_number': f'SYNTHETIC-{index:06d}'}, 'quantity': 1} for index in range(min(size, 100))]}),
        ('overlapping_bom', 'check_inventory', {'items': [
            {'filters': {'part_category': 'resistor', 'value': '10k'}, 'quantity': 1},
            {'filters': {'part_number': 'SYNTHETIC-000000'}, 'quantity': 1}]}),
    ]


async def measure(size: int, *, repeats: int = 3, limit: int = 20) -> dict:
    if type(size) is not int or not 1 <= size <= 100_000:
        raise ValueError('Stock size must be between 1 and 100000')
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError('Use between 1 and 20 repetitions')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Page limit must be between 1 and 100')
    with TemporaryDirectory(prefix='parts-bin-scale-') as directory:
        repository = SQLitePartsBinRepository(Path(directory) / 'inventory.db')
        seed(repository, size)
        measured = MeasuredInventory(repository.database)
        repository.inventory = measured
        service = PartsBinService(repository)
        registry = PartsBinToolRegistry(service)
        measurements = []
        for name, tool, arguments in workloads(size, limit):
            samples = []
            # Report the first call separately; do not imply an OS cold-cache test.
            for _ in range(repeats):
                measured.reads.clear()
                started = perf_counter()
                response = await registry.execute(tool, arguments)
                elapsed = (perf_counter() - started) * 1000
                if not response['ok']:
                    raise RuntimeError(f'{name}: {response["error"]}')
                result = response['result']
                returned = sum(len(result.get(key, [])) for key in ('parts', 'candidates', 'matches', 'incomplete', 'partial_matches'))
                if tool == 'check_inventory':
                    returned = sum(len(item['parts']) for item in result['items'])
                samples.append({'elapsed_ms': elapsed, 'response_bytes': len(json.dumps(response, ensure_ascii=False).encode()),
                                'returned_part_records': returned,
                                **({'bom_candidate_matches': sum(item['match_count'] for item in result['items']),
                                    'overlapping_requirement_groups': len(result['overlapping_requirements'])}
                                   if tool == 'check_inventory' else {}),
                                'reads': dict(measured.reads),
                                'counts': {key: result[key] for key in ('count', 'match_count', 'incomplete_count', 'partial_match_count', 'next_offset') if key in result}})
            measurements.append({'workload': name, 'tool': tool, 'samples': samples,
                                 'median_elapsed_ms': median(sample['elapsed_ms'] for sample in samples)})
        return {'inventory_records': size, 'page_limit': limit, 'measurements': measurements}


async def run(sizes: list[int], repeats: int, limit: int) -> dict:
    return {'dataset': 'Synthetic one-category SQLite stress case; no application configuration or production data.',
            'interpretation': 'Adapter counts are logical calls and materialized rows, not disk reads or query plans. OS cache state is uncontrolled. BOM responses may be larger than paged searches. Timings include tool validation and serialization-size measurement is separate.',
            'runs': [await measure(size, repeats=repeats, limit=limit) for size in sizes]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[100, 1000, 5000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.sizes, args.repeats, args.limit))
    except ValueError as exc:
        parser.error(str(exc))
    # Exclusive creation keeps an earlier measurement intact.
    with args.output.open('x') as output:
        json.dump(report, output, indent=2)
        output.write('\n')


if __name__ == '__main__':
    main()
