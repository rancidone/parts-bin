"""Aurora DSQL IAM connection configuration."""

from dataclasses import dataclass
import boto3
import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class DSQLConfig:
    endpoint: str
    region: str
    user: str = "parts_bin"

    def __post_init__(self):
        suffix = f".dsql.{self.region}.on.aws"
        if not self.region or not self.endpoint.endswith(suffix) or '/' in self.endpoint:
            raise ValueError("Supply a DSQL hostname matching the AWS region")
        if not self.user:
            raise ValueError("Supply a database role")

    def connect(self):
        client = boto3.client("dsql", region_name=self.region)
        generate = (client.generate_db_connect_admin_auth_token if self.user == "admin"
                    else client.generate_db_connect_auth_token)
        token = generate(self.endpoint, self.region, ExpiresIn=900)
        return psycopg.connect(host=self.endpoint, dbname="postgres", user=self.user,
            password=token, sslmode="verify-full", sslrootcert="system",
            connect_timeout=15, autocommit=True, row_factory=dict_row)
