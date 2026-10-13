"""Aurora DSQL uses the shared PostgreSQL SQL adapter with IAM authentication."""

from db.postgres import PostgresPartsBinRepository
from .connection import DSQLConfig


class DSQLPartsBinRepository(PostgresPartsBinRepository):
    """Use DSQLConfig.connect for IAM tokens and verified TLS."""

    def __init__(self, config: DSQLConfig, *, connect=None):
        # The parent uses config only to connect; identity belongs to the cluster.
        super().__init__(config, connect=connect, storage_id=f"dsql:{config.region}:{config.endpoint}")
