"""Disposable schema lifecycle, including failed initialization and reuse."""

from unittest.mock import patch

import pytest

from db.postgres import disposable


def schema_exists(repository):
    schema = repository.storage_id.removeprefix('postgres-test:')
    with repository.connect() as connection:
        return connection.execute('SELECT 1 FROM pg_namespace WHERE nspname = %s', (schema,)).fetchone() is not None


def test_cleanup_removes_schema_and_same_key_can_initialize_again(tmp_path):
    key = tmp_path / 'repeat'
    first = disposable.isolated_repository(key)
    assert schema_exists(first)
    disposable.cleanup()
    assert not schema_exists(first)
    second = disposable.isolated_repository(key)
    assert schema_exists(second)
    assert second.inventory.search({}) == []


def test_failed_initialization_remains_tracked_for_cleanup(tmp_path):
    key = tmp_path / 'partial'
    connect = None
    def fail(connection_factory):
        nonlocal connect
        connect = connection_factory
        # A partially initialized schema has real relations to reclaim.
        with connect() as connection:
            connection.execute('CREATE TABLE partial_setup (id integer)')
        raise RuntimeError('Interrupted setup')
    with patch.object(disposable, 'initialize', fail):
        with pytest.raises(RuntimeError, match='Interrupted setup'):
            disposable.isolated_repository(key)
    with connect() as connection:
        schema = connection.execute('SHOW search_path').fetchone()['search_path']
    disposable.cleanup()
    with connect() as connection:
        assert connection.execute('SELECT 1 FROM pg_namespace WHERE nspname = %s', (schema,)).fetchone() is None
    assert disposable.isolated_repository(key).inventory.search({}) == []
