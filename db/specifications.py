"""SQLite storage for electrical facts and their independent pending review."""

import json

from domain.repositories import RepositoryConflict
from . import persistence

_METADATA = ('part_category', 'profile', 'value', 'package', 'part_number', 'manufacturer')


def _facts(conn, part_id):
    row = conn.execute('SELECT facts_json FROM part_specifications WHERE part_id = ?', (part_id,)).fetchone()
    return [] if row is None else json.loads(row['facts_json'])


def _snapshot(conn, part_id):
    row = conn.execute('SELECT * FROM parts WHERE id = ?', (part_id,)).fetchone()
    return None if row is None else {'metadata': {key: row[key] for key in _METADATA}, 'facts': _facts(conn, part_id)}


def facts(database, part_id):
    conn = persistence._connect(database)
    try:
        return _facts(conn, part_id)
    finally:
        conn.close()


def facts_for_parts(database, part_ids: list[int]) -> dict[int, list[dict]]:
    identifiers = list(dict.fromkeys(part_ids))
    if not identifiers:
        return {}
    conn = persistence._connect(database)
    try:
        found = {}
        with conn:
            if not isinstance(conn, persistence.TransactionConnection):
                conn.execute('BEGIN')
            # Bound parameter counts across SQLite builds. All chunks share one
            # read snapshot, or the caller's existing unit of work.
            for offset in range(0, len(identifiers), 500):
                chunk = identifiers[offset:offset + 500]
                placeholders = ','.join('?' for _ in chunk)
                rows = conn.execute(
                    f'SELECT part_id, facts_json FROM part_specifications WHERE part_id IN ({placeholders})',
                    chunk).fetchall()
                found.update({row['part_id']: json.loads(row['facts_json']) for row in rows})
        return found
    finally:
        conn.close()


def reviews(database):
    conn = persistence._connect(database)
    try:
        return {row['part_id']: {'facts': json.loads(row['facts_json']), 'snapshot': json.loads(row['snapshot_json'])}
                for row in conn.execute('SELECT * FROM part_specification_reviews ORDER BY part_id').fetchall()}
    finally:
        conn.close()


def stage(database, original, proposed, existing):
    conn = persistence._connect(database)
    try:
        with conn:
            if not isinstance(conn, persistence.TransactionConnection):
                conn.execute('BEGIN IMMEDIATE')
            snapshot = {'metadata': {key: getattr(original, key) for key in _METADATA}, 'facts': existing}
            if snapshot != _snapshot(conn, original.id):
                return False
            if conn.execute('SELECT 1 FROM part_specification_reviews WHERE part_id = ?', (original.id,)).fetchone():
                return False
            conn.execute('INSERT INTO part_specification_reviews VALUES (?, ?, ?)',
                         (original.id, json.dumps(proposed), json.dumps(snapshot)))
            return True
    finally:
        conn.close()


def apply(database, part_id):
    conn = persistence._connect(database)
    try:
        with conn:
            if not isinstance(conn, persistence.TransactionConnection):
                conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM part_specification_reviews WHERE part_id = ?', (part_id,)).fetchone()
            if row is None or json.loads(row['snapshot_json']) != _snapshot(conn, part_id):
                raise RepositoryConflict('Specification review or target changed')
            current = {fact['name']: fact for fact in _facts(conn, part_id)}
            current.update({fact['name']: fact for fact in json.loads(row['facts_json'])})
            conn.execute('INSERT INTO part_specifications VALUES (?, ?) ON CONFLICT(part_id) DO UPDATE SET facts_json = excluded.facts_json',
                         (part_id, json.dumps(list(current.values()))))
            conn.execute('UPDATE parts SET updated_at = ? WHERE id = ?', (persistence._now(), part_id))
            conn.execute('DELETE FROM part_specification_reviews WHERE part_id = ?', (part_id,))
    finally:
        conn.close()


def reject(database, part_id):
    conn = persistence._connect(database)
    try:
        with conn:
            conn.execute('DELETE FROM part_specification_reviews WHERE part_id = ?', (part_id,))
    finally:
        conn.close()
