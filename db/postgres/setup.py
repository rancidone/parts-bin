"""Explicit schema installation: python -m db.postgres.setup --host HOST --database DB --user USER."""

import argparse
from pathlib import Path

from .connection import PostgresConfig


def initialize(connect):
    # DSQL permits only one DDL statement per transaction. Autocommit executes
    # each statement independently; rerunning an interrupted setup is safe.
    with connect() as connection:
        for statement in Path(__file__).with_name('schema.sql').read_text().split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('INSERT INTO pb_revision VALUES (1, 0, 1, 1) ON CONFLICT(id) DO NOTHING')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--user', required=True)
    args = parser.parse_args()
    initialize(PostgresConfig(args.host, args.database, args.user).connect)


if __name__ == '__main__':
    main()
