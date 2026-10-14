"""Explicit schema installation: python -m db.postgres.setup --host HOST --database DB --user USER."""

import argparse
import tomllib
from pathlib import Path

from .connection import PostgresConfig


def initialize(connect):
    # Execute schema statements independently so rerunning interrupted setup is safe.
    with connect() as connection:
        for statement in Path(__file__).with_name('schema.sql').read_text().split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('INSERT INTO pb_revision VALUES (1, 0, 1, 1) ON CONFLICT(id) DO NOTHING')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='Read connection settings from protected installation config')
    parser.add_argument('--host')
    parser.add_argument('--database')
    parser.add_argument('--user')
    args = parser.parse_args()
    if args.config:
        if args.host or args.database or args.user:
            parser.error('Use --config or connection names, not both')
        config = PostgresConfig(**tomllib.loads(args.config.read_text())['db'])
    else:
        if not all((args.host, args.database, args.user)):
            parser.error('Supply --config or --host, --database and --user')
        config = PostgresConfig(args.host, args.database, args.user)
    initialize(config.connect)


if __name__ == '__main__':
    main()
