"""Disposable enrichment results and cooldowns on the installation database."""

import json
from ingestion.cache import CacheBusyError
from .connection import Adapter, access
from .records import encode


class PostgresEnrichmentCache(Adapter):
    @access()
    def previous(self, key):
        row = self.execute('SELECT result FROM supplied_enrichment_cache WHERE key = %s', (key,)).fetchone()
        return json.loads(row['result']) if row and row['result'] else None

    @access(write=True)
    def acquire(self, key, *, now, lease_until, refresh=False):
        row = self.execute('SELECT * FROM supplied_enrichment_cache WHERE key = %s', (key,)).fetchone()
        if row and row['lease_until'] > now:
            raise CacheBusyError('Enrichment is active or cooling down after a failure; try later')
        if row and row['expires'] > now and row['result'] and not refresh:
            return json.loads(row['result'])
        self.execute('''INSERT INTO supplied_enrichment_cache VALUES (%s, 0, %s, NULL)
            ON CONFLICT(key) DO UPDATE SET expires = 0, lease_until = excluded.lease_until, result = NULL''', (key, lease_until))
        return None

    @access(write=True)
    def save(self, key, result, *, expires):
        self.execute('UPDATE supplied_enrichment_cache SET expires = %s, lease_until = 0, result = %s WHERE key = %s',
                     (expires, encode(result), key))
