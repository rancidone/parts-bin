"""SQLite storage for disposable supplied-source results and cooldown leases."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ingestion.cache import CacheBusyError


class SQLiteEnrichmentCache:
    """Disposable local cache with cross-process leases; not a durable job queue."""

    def __init__(self, path: str | Path):
        self.path = path
        with self.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS supplied_enrichment_cache (
                key TEXT PRIMARY KEY, expires REAL NOT NULL, lease_until REAL NOT NULL,
                result TEXT)""")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def previous(self, key: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT result FROM supplied_enrichment_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def acquire(self, key: str, *, now: float, lease_until: float, refresh: bool = False) -> dict | None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT expires, lease_until, result FROM supplied_enrichment_cache WHERE key = ?", (key,)).fetchone()
            if row and row[1] > now:
                raise CacheBusyError("Enrichment is active or cooling down after a failure; try later")
            if row and row[0] > now and row[2] and not refresh:
                return json.loads(row[2])
            conn.execute("INSERT OR REPLACE INTO supplied_enrichment_cache VALUES (?, 0, ?, NULL)",
                         (key, lease_until))
        return None

    def save(self, key: str, result: dict, *, expires: float) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE supplied_enrichment_cache SET expires = ?, lease_until = 0, result = ? WHERE key = ?",
                         (expires, json.dumps(result), key))

