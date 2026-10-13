"""Import protected SQLite snapshots into an empty PostgreSQL installation."""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tomllib

from psycopg import sql

from . import PostgresPartsBinRepository, PostgresConfig
from .inventory import passive_identity
from .records import encode
from .setup import initialize

INVENTORY_TABLES = ('parts', 'part_field_provenance', 'part_pending_field_review',
    'part_specifications', 'part_specification_reviews', 'agent_approvals', 'agent_operations',
    'agent_executions', 'agent_execution_approvals')
CONVERSATION_TABLES = ('agent_threads', 'agent_events', 'agent_event_keys')


def snapshot(path):
    path = Path(path).resolve()
    # Online-backup files can retain WAL mode without needing live sidecars.
    # Immutable reads never create SHM/WAL files; reject unsnapshotted writes.
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('Use a standalone SQLite backup snapshot, not a database with outstanding journal/WAL data')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite snapshot failed integrity check')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        data = {table: [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
                for table in (*INVENTORY_TABLES, *CONVERSATION_TABLES, 'agent_codex_sessions') if table in tables}
        if 'agent_threads' in tables:
            data['agent_threads'] = [{**dict(row), 'thread_order': index}
                for index, row in enumerate(connection.execute('SELECT * FROM agent_threads ORDER BY rowid'), 1)]
        sequence = connection.execute("SELECT seq FROM sqlite_sequence WHERE name='parts'").fetchone() if 'sqlite_sequence' in tables else None
        data['next_part_id'] = max([row['id'] for row in data.get('parts', [])] + [sequence[0] if sequence else 0]) + 1
        return data
    finally:
        connection.close()


def canonical(rows):
    return sorted(encode(row) for row in rows)


def projected(connection, table, rows):
    columns = [row['column_name'] for row in connection.execute(
        """SELECT column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position""", (table,)).fetchall()]
    converted = []
    for source in rows:
        values = dict(source)
        if table == 'parts':
            values['passive_identity'] = passive_identity(values)
            values.setdefault('datasheet_url', None)
        if table == 'part_field_provenance':
            values.setdefault('evidence', None)
        converted.append({name: values[name] for name in columns})
    return columns, converted


def verify_snapshots(repository, inventory_path, conversations_path=None):
    inventory = snapshot(inventory_path)
    conversations = inventory if conversations_path is None else snapshot(conversations_path)
    if 'parts' not in inventory:
        raise ValueError('Inventory snapshot has no parts table')
    counts = {}
    with repository.unit(write=False) as unit:
        for table in (*INVENTORY_TABLES, *CONVERSATION_TABLES):
            source = inventory if table in INVENTORY_TABLES else conversations
            _, expected = projected(unit.connection, table, source.get(table, []))
            actual = unit.connection.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
            if canonical(actual) != canonical(expected):
                raise ValueError(f'Snapshot verification failed for {table}')
            counts[table] = len(expected)
        expected_legacy = [{'thread_id': row['thread_id'], 'data_json': encode(row)}
                           for row in conversations.get('agent_codex_sessions', [])]
        if canonical(unit.connection.execute('SELECT * FROM pb_legacy_sessions').fetchall()) != canonical(expected_legacy):
            raise ValueError('Snapshot verification failed for legacy sessions')
        allocator = unit.connection.execute('SELECT next_part_id, next_thread_order FROM pb_revision WHERE id = 1').fetchone()
        if allocator != {'next_part_id': inventory['next_part_id'], 'next_thread_order': counts['agent_threads'] + 1}:
            raise ValueError('Snapshot verification failed for ID allocation')
    return counts


def import_snapshots(repository, inventory_path, conversations_path=None):
    inventory = snapshot(inventory_path)
    conversations = inventory if conversations_path is None else snapshot(conversations_path)
    if 'parts' not in inventory:
        raise ValueError('Inventory snapshot has no parts table')
    raw = {table: (inventory if table in INVENTORY_TABLES else conversations).get(table, [])
           for table in (*INVENTORY_TABLES, *CONVERSATION_TABLES)}
    source_digest = hashlib.sha256(encode({'tables': raw, 'next_part_id': inventory['next_part_id'],
        'legacy': conversations.get('agent_codex_sessions', [])}).encode()).hexdigest()
    counts = {table: len(rows) for table, rows in raw.items()}
    with repository.transaction() as unit:
        connection = unit.connection
        previous = connection.execute('SELECT counts_json FROM pb_imports WHERE source_digest = %s', (source_digest,)).fetchone()
        if previous:
            return json.loads(previous['counts_json'])
        for table in (*INVENTORY_TABLES, *CONVERSATION_TABLES, 'pb_imports', 'pb_legacy_sessions'):
            if connection.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(table))).fetchone():
                raise ValueError('Import requires an empty target; it never overwrites an installation')
        for table, rows in raw.items():
            columns, converted = projected(connection, table, rows)
            for row in converted:
                connection.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
                    sql.SQL(', ').join(map(sql.Identifier, columns)), sql.SQL(', ').join(sql.Placeholder() for _ in columns)), tuple(row.values()))
            imported = connection.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
            if canonical(imported) != canonical(converted):
                raise ValueError(f'Imported data verification failed for {table}')
        for row in conversations.get('agent_codex_sessions', []):
            connection.execute('INSERT INTO pb_legacy_sessions VALUES (%s, %s)', (row['thread_id'], encode(row)))
        connection.execute('UPDATE pb_revision SET next_part_id = %s, next_thread_order = %s WHERE id = 1',
            (inventory['next_part_id'], len(raw['agent_threads']) + 1))
        connection.execute('INSERT INTO pb_imports VALUES (%s, %s)', (source_digest, encode(counts)))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--conversations', type=Path)
    parser.add_argument('--verify', action='store_true', help='Compare a migrated/restored target without writing')
    args = parser.parse_args()
    with args.config.open('rb') as file:
        config = PostgresConfig(**tomllib.load(file)['db'])
    if not args.verify:
        initialize(config.connect)
    operation = verify_snapshots if args.verify else import_snapshots
    print(json.dumps(operation(PostgresPartsBinRepository(config), args.inventory, args.conversations), sort_keys=True))


if __name__ == '__main__':
    main()
