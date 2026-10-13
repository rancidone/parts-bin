"""Test storage factories: every fixture uses an isolated PostgreSQL schema."""
from db.postgres.disposable import isolated_repository as TestRepository


def TestConversations(key):
    return TestRepository(key).conversations


def TestCache(key):
    return TestRepository(key).cache


def connection(key):
    return TestRepository(key).connect()


def historical_thread(key, thread_id, runtime):
    with TestRepository(key).transaction() as unit:
        unit.connection.execute('INSERT INTO agent_threads VALUES (%s, %s, %s)', (thread_id, runtime, 1000))
