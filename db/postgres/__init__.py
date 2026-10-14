"""PostgreSQL persistence; schema setup is an explicit operator action."""

from contextlib import contextmanager

from .connection import PostgresConfig, unit
from .inventory import PostgresInventoryRepository
from .records import PostgresApprovalRepository
from .execution import PostgresExecutionRepository, PostgresOperationRepository
from .conversations import PostgresConversationRepository
from .dispatch import PostgresDispatchRepository
from .cache import PostgresEnrichmentCache


class PostgresPartsBinRepository:
    def __init__(self, config: PostgresConfig, *, connect=None, storage_id=None):
        self.config = config
        self.connect = connect or config.connect
        self.storage_id = storage_id or f"postgres:{config.host}:{config.port}:{config.database}"
        self.connection = None
        self._adapters()

    @property
    def in_transaction(self) -> bool:
        return self.connection is not None

    def _adapters(self):
        self.inventory = PostgresInventoryRepository(self)
        self.approvals = PostgresApprovalRepository(self)
        self.operations = PostgresOperationRepository(self)
        self.executions = PostgresExecutionRepository(self)
        self.conversations = PostgresConversationRepository(self)
        self.cache = PostgresEnrichmentCache(self)
        self.dispatch = PostgresDispatchRepository(self)

    @contextmanager
    def unit(self, *, write):
        if self.connection is not None:
            yield self
            return
        with unit(self.connect, write=write) as connection:
            bound = object.__new__(type(self))
            bound.config, bound.connect, bound.storage_id = self.config, self.connect, self.storage_id
            bound.connection = connection
            bound._adapters()
            yield bound

    def transaction(self):
        return self.unit(write=True)


def from_config(config: dict) -> PostgresPartsBinRepository:
    """Read the one PostgreSQL installation configuration; never create schema."""
    return PostgresPartsBinRepository(PostgresConfig(**config['db']))
