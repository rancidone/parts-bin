"""Read-only SQLite import preserves stock, evidence, identities and retries."""
import json
import sqlite3

import pytest

from db.postgres.migrate import import_snapshots, verify_snapshots
from domain import AddPartRequest, PartFields, PartsBinService
from tests.storage import TestRepository


def legacy_snapshot(tmp_path):
    inventory, conversations = tmp_path / 'inventory.db', tmp_path / 'events.db'
    with sqlite3.connect(inventory) as connection:
        connection.executescript('''
            CREATE TABLE parts (id INTEGER PRIMARY KEY AUTOINCREMENT, part_category TEXT, profile TEXT,
              value TEXT, package TEXT, part_number TEXT, quantity INTEGER, manufacturer TEXT,
              description TEXT, datasheet_url TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE part_field_provenance (part_id INTEGER, field_name TEXT, field_value TEXT,
              source_tier TEXT, source_kind TEXT, source_locator TEXT, extraction_method TEXT,
              confidence_marker TEXT, conflict_status TEXT, normalization_method TEXT, evidence TEXT,
              competing_candidates TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE part_specifications (part_id INTEGER, facts_json TEXT);
            CREATE TABLE agent_operations (operation_id TEXT, tool_name TEXT, arguments_json TEXT, result_json TEXT);
            CREATE TABLE agent_executions (execution_id TEXT, thread_id TEXT, context_json TEXT, status TEXT,
              worker_id TEXT, lease_until REAL);
        ''')
        connection.execute('INSERT INTO parts VALUES (42, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('resistor', 'passive', '10K', '0402', 'EXACT-10K-F', 9, 'Original', 'Drawer A',
             'https://example.com/source.pdf', 'original-created', 'original-updated'))
        connection.execute('INSERT INTO part_field_provenance VALUES (42, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('description', 'Drawer A', 'user', 'label', None, 'manual', None, 'clear', None,
             'Original label', '[]', 'original-created', 'original-updated'))
        connection.execute('INSERT INTO part_specifications VALUES (42, ?)', (json.dumps([{'name': 'original-fact'}]),))
        connection.execute('INSERT INTO agent_operations VALUES (?, ?, ?, ?)', ('stock', 'add_stock', '{"part_id":42,"quantity":2}', '{"id":42,"quantity":9}'))
        connection.execute('INSERT INTO agent_executions VALUES (?, ?, ?, ?, ?, ?)', ('run', 'history', '{"input_digest":"input"}', 'completed', None, 0))
        # Deleted IDs must never be reused by the new allocator.
        connection.execute('UPDATE sqlite_sequence SET seq = 50 WHERE name = "parts"')
    with sqlite3.connect(conversations) as connection:
        connection.executescript('''
            CREATE TABLE agent_threads (thread_id TEXT PRIMARY KEY, runtime TEXT);
            CREATE TABLE agent_events (thread_id TEXT, sequence INTEGER, kind TEXT, runtime TEXT, data_json TEXT);
            CREATE TABLE agent_event_keys (event_id TEXT, thread_id TEXT, sequence INTEGER);
            CREATE TABLE agent_codex_sessions (thread_id TEXT, codex_session_id TEXT);
        ''')
        connection.executemany('INSERT INTO agent_threads VALUES (?, ?)', [('old', 'codex'), ('history', 'openai')])
        connection.execute('INSERT INTO agent_events VALUES (?, ?, ?, ?, ?)', ('old', 3, 'assistant_text', 'codex', '{"text":"Preserved answer"}'))
        connection.execute('INSERT INTO agent_event_keys VALUES (?, ?, ?)', ('saved-event', 'old', 3))
        connection.execute('INSERT INTO agent_codex_sessions VALUES (?, ?)', ('old', 'original-session'))
    return inventory, conversations


def test_import_preserves_every_projected_row_and_never_reimports_over_later_edits(tmp_path):
    inventory, conversations = legacy_snapshot(tmp_path)
    original = inventory.read_bytes(), conversations.read_bytes()
    target = TestRepository(tmp_path / 'target')
    counts = import_snapshots(target, inventory, conversations)
    assert counts['parts'] == 1 and counts['agent_threads'] == 2
    assert verify_snapshots(target, inventory, conversations) == counts
    assert (inventory.read_bytes(), conversations.read_bytes()) == original
    part = target.inventory.get(42)
    assert part.value == '10K' and part.quantity == 9 and part.description == 'Drawer A'
    assert part.created_at == 'original-created'
    assert target.inventory.list_provenance(42)[0]['evidence'] == 'Original label'
    assert target.inventory.specifications(42) == [{'name': 'original-fact'}]
    assert target.operations.get('stock').result == {'id': 42, 'quantity': 9}
    assert target.conversations.runtime_for('old') == 'codex'
    assert [event.sequence for event in target.conversations.events('old')] == [3]
    assert [thread.thread_id for thread in target.conversations.threads()] == ['history', 'old']
    target.inventory.increment_stock(42, 1)
    assert import_snapshots(target, inventory, conversations) == counts
    assert target.inventory.get(42).quantity == 10
    with pytest.raises(ValueError, match="verification failed for parts"):
        verify_snapshots(target, inventory, conversations)
    added = PartsBinService(target).add_part(AddPartRequest(PartFields('resistor', 'passive', 1, '22k', '0603')))
    assert added.id == 51


def test_import_refuses_nonempty_target_and_rolls_back_bad_source(tmp_path):
    inventory, conversations = legacy_snapshot(tmp_path)
    target = TestRepository(tmp_path / 'target')
    target.inventory.insert(vars(PartFields('resistor', 'passive', 1, '1k', '0603')))
    with pytest.raises(ValueError, match='empty target'):
        import_snapshots(target, inventory, conversations)
    empty = TestRepository(tmp_path / 'empty')
    with sqlite3.connect(inventory) as connection:
        connection.execute("UPDATE parts SET profile = 'invalid'")
    from domain.repositories import RepositoryConflict
    with pytest.raises(RepositoryConflict):
        import_snapshots(empty, inventory, conversations)
    assert empty.inventory.search({}) == []
    assert empty.operations.get('stock') is None


def test_wal_mode_snapshot_never_creates_sidecars_and_rejects_live_wal(tmp_path):
    inventory, conversations = legacy_snapshot(tmp_path)
    with sqlite3.connect(inventory) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
    from db.postgres.migrate import snapshot
    original = inventory.read_bytes()
    assert snapshot(inventory)['parts'][0]['id'] == 42
    assert inventory.read_bytes() == original
    assert not (tmp_path / 'inventory.db-shm').exists()
    with sqlite3.connect(inventory) as connection:
        connection.execute("UPDATE parts SET quantity=10")
        connection.commit()
        with pytest.raises(ValueError, match='standalone SQLite backup'):
            snapshot(inventory)
