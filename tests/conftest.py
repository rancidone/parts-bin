"""Recorded tests share an explicitly isolated PostgreSQL test database."""
import os
from uuid import uuid4
import pytest
from db.postgres.disposable import cleanup


@pytest.fixture(scope='session', autouse=True)
def isolated_postgres():
    os.environ.setdefault('PARTS_BIN_TEST_NAMESPACE', uuid4().hex)
    if not os.environ.get('PARTS_BIN_TEST_POSTGRES_DSN'):
        raise pytest.UsageError('Set PARTS_BIN_TEST_POSTGRES_DSN to a disposable PostgreSQL database; see docs/operations.md')
    yield
    cleanup()


@pytest.fixture(autouse=True)
def release_test_schemas(isolated_postgres):
    """Release each test's tables before the next test, including failed tests."""
    yield
    cleanup()
