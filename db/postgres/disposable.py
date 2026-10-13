"""Isolated PostgreSQL schemas for tests and recorded/live evaluation artifacts."""

import hashlib
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from . import PostgresPartsBinRepository, PostgresConfig
from .setup import initialize


_initialized = set()


def isolated_repository(key):
    dsn = os.environ.get('PARTS_BIN_TEST_POSTGRES_DSN')
    namespace = os.environ.get('PARTS_BIN_TEST_NAMESPACE')
    if not dsn or not namespace:
        raise RuntimeError('Set PARTS_BIN_TEST_POSTGRES_DSN and PARTS_BIN_TEST_NAMESPACE for isolated PostgreSQL tests/evaluations')
    if str(key) == ':memory:':
        key = uuid4().hex
    schema = 'pb_test_' + hashlib.sha256((namespace + str(key)).encode()).hexdigest()[:24]

    def base():
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)

    def connect():
        connection = base()
        connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        return connection

    if schema not in _initialized:
        with base() as connection:
            connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
        initialize(connect)
        _initialized.add(schema)
    repository = PostgresPartsBinRepository(PostgresConfig('isolated', 'test', 'test'), connect=connect,
                                           storage_id=f'postgres-test:{schema}')
    repository.isolation_key = key
    repository.conversations.isolation_key = key
    repository.cache.isolation_key = key
    return repository


def cleanup():
    if not _initialized:
        return
    dsn = os.environ['PARTS_BIN_TEST_POSTGRES_DSN']
    with psycopg.connect(dsn, autocommit=True) as connection:
        for schema in _initialized:
            connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
    _initialized.clear()
