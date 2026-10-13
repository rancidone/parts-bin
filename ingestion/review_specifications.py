"""Stage operator-inspected electrical evidence; never apply it without approval."""

import argparse
import json
from pathlib import Path

from db.postgres import from_config
import tomllib
from domain import DomainError, GetPartRequest, PartsBinService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('part_id', type=int)
    parser.add_argument('candidate', type=Path, help='JSON array of facts with bounded source passages')
    parser.add_argument('--config', type=Path, required=True, help='Protected PostgreSQL installation config')
    args = parser.parse_args()
    try:
        if args.candidate.stat().st_size > 100_000:
            raise ValueError('Candidate exceeds size limit')
        facts = json.loads(args.candidate.read_text())
        with args.config.open('rb') as file:
            service = PartsBinService(from_config(tomllib.load(file)))
        service.stage_specifications(service.get(GetPartRequest(args.part_id)), facts)
    except (DomainError, ValueError, OSError) as exc:
        parser.exit(1, f'Specification review failed: {exc}\n')
    print(json.dumps(service.get_specifications(args.part_id), indent=2))


if __name__ == '__main__':
    main()
