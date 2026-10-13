"""Shared contracts on local PostgreSQL, and an opt-in live DSQL schema."""

import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from db.dsql import DSQLPartsBinRepository, DSQLConfig
from db.dsql.setup import initialize


@pytest.fixture(params=['postgres', 'dsql'])
def repository(request, tmp_path):
    if request.param == 'postgres':
        dsn = os.environ.get('PARTS_BIN_TEST_POSTGRES_DSN')
        if not dsn:
            pytest.skip('Set PARTS_BIN_TEST_POSTGRES_DSN for local PostgreSQL protocol tests')
        base = lambda: psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        config = DSQLConfig('test.dsql.us-east-1.on.aws', 'us-east-1')
    else:
        endpoint = os.environ.get('PARTS_BIN_TEST_DSQL_ENDPOINT')
        if not endpoint:
            pytest.skip('Operator-provisioned DSQL cluster required for live service checks')
        config = DSQLConfig(endpoint, os.environ['PARTS_BIN_TEST_DSQL_REGION'],
                            os.environ.get('PARTS_BIN_TEST_DSQL_USER', 'parts_bin'))
        base = config.connect
    schema = 'pb_test_' + uuid4().hex
    with base() as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))

    def connect():
        connection = base()
        connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        return connection

    try:
        initialize(connect)
        yield DSQLPartsBinRepository(config, connect=connect)
    finally:
        with connect() as connection:
            # DSQL cleanup uses one DDL statement per autocommit transaction.
            for table in ('agent_dispatch', 'agent_webhook_inbox', 'agent_worker_heartbeat', 'pb_legacy_sessions', 'pb_imports', 'supplied_enrichment_cache', 'agent_event_keys', 'agent_events', 'agent_threads', 'agent_execution_approvals',
                          'agent_executions', 'agent_operations', 'agent_approvals', 'part_specification_reviews',
                          'part_specifications', 'part_pending_field_review', 'part_field_provenance', 'parts', 'pb_revision'):
                connection.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(sql.Identifier(table)))
            connection.execute(sql.SQL('DROP SCHEMA {}').format(sql.Identifier(schema)))
