"""Preview or explicitly migrate nominal values, with a protected inventory backup."""

import argparse
import json
import os
from pathlib import Path
import tomllib

from db.postgres import from_config
from domain import PartsBinService


def migrate(repository, *, backup: Path | None = None) -> dict:
    service = PartsBinService(repository)
    if backup is None:
        return service.plan_nominal_normalization()
    with service.transaction() as (bound, unit):
        plan = bound.plan_nominal_normalization()
        if plan['conflicting_part_ids']:
            return {**plan, 'applied': False}
        parts = bound.list()
        snapshot = {'parts': [vars(part) for part in parts],
                    'pending_reviews': unit.inventory.list_pending_reviews(),
                    'specifications': unit.inventory.specifications_for_parts([part.id for part in parts]),
                    'specification_reviews': unit.inventory.specification_reviews(),
                    'provenance': {part.id: unit.inventory.list_provenance(part.id) for part in parts}}
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(snapshot, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        return {**bound.normalize_nominal_values(), 'applied': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.apply != bool(args.backup):
        parser.error('--apply requires --backup and --backup requires --apply')
    result = migrate(from_config(tomllib.loads(args.config.read_text())), backup=args.backup)
    print(json.dumps(result, ensure_ascii=False))
    if result.get('applied') is False:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
