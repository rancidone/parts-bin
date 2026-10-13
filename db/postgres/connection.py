"""PostgreSQL connections and short, snapshot-safe units of work."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
import time

import psycopg
from psycopg.rows import dict_row

from domain.repositories import RepositoryBusy, RepositoryConflict


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    database: str
    user: str
    port: int = 5432
    password: str | None = field(default=None, repr=False)
    sslmode: str = "prefer"

    def connect(self):
        parameters = dict(host=self.host, dbname=self.database, user=self.user,
                          port=self.port, sslmode=self.sslmode, connect_timeout=15,
                          autocommit=True, row_factory=dict_row)
        if self.password is not None:
            parameters['password'] = self.password
        return psycopg.connect(**parameters)


def access(*, write=False):
    """Retry only self-contained database calls, never arbitrary transaction bodies."""
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            if self.connection is not None:
                return method(self, *args, **kwargs)
            for attempt in range(6):
                try:
                    with self.owner.unit(write=write) as owner:
                        bound = type(self)(owner)
                        return method(bound, *args, **kwargs)
                except RepositoryBusy:
                    if attempt == 5:
                        raise
                    time.sleep(0.01 * 2 ** attempt)
        return wrapped
    return decorate


class Adapter:
    def __init__(self, owner):
        self.owner = owner
        self.connection = owner.connection

    def execute(self, sql, parameters=()):
        try:
            return self.connection.execute(sql, parameters)
        except psycopg.Error as exc:
            if exc.sqlstate == "40001":
                raise RepositoryBusy("Concurrent database write; retry the operation") from exc
            if exc.sqlstate and exc.sqlstate.startswith("23"):
                raise RepositoryConflict("Inventory or storage integrity constraint violated") from exc
            raise


@contextmanager
def unit(connect, *, write):
    connection = connect()
    try:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
        if write:
            # A common write dependency closes snapshot-isolation write-skew holes
            # in approval checks, event sequencing, normalized identity and leases.
            cursor = connection.execute("UPDATE pb_revision SET revision = revision + 1 WHERE id = 1")
            if cursor.rowcount != 1:
                raise RuntimeError("Database schema has not been initialized")
        yield connection
        connection.execute("COMMIT")
    except BaseException as exc:
        try:
            connection.execute("ROLLBACK")
        except psycopg.Error:
            pass
        if isinstance(exc, psycopg.Error):
            if exc.sqlstate == "40001":
                raise RepositoryBusy("Concurrent database write; retry the operation") from exc
            if exc.sqlstate and exc.sqlstate.startswith("23"):
                raise RepositoryConflict("Inventory or storage integrity constraint violated") from exc
        raise
    finally:
        connection.close()
