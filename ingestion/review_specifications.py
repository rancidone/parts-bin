"""Stage operator-inspected electrical evidence; never apply it without approval."""

import argparse
import json
from pathlib import Path

from db.repository import SQLitePartsBinRepository
from domain import DomainError, GetPartRequest, PartsBinService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('part_id', type=int)
    parser.add_argument('candidate', type=Path, help='JSON array of facts with bounded source passages')
    parser.add_argument('--database', type=Path, required=True, help='Existing local inventory database')
    args = parser.parse_args()
    try:
        if not args.database.is_file():
            raise ValueError('Inventory database must already exist')
        if args.candidate.stat().st_size > 100_000:
            raise ValueError('Candidate exceeds size limit')
        facts = json.loads(args.candidate.read_text())
        service = PartsBinService(SQLitePartsBinRepository(args.database))
        service.stage_specifications(service.get(GetPartRequest(args.part_id)), facts)
    except (DomainError, ValueError, OSError) as exc:
        parser.exit(1, f'Specification review failed: {exc}\n')
    print(json.dumps(service.get_specifications(args.part_id), indent=2))


if __name__ == '__main__':
    main()
