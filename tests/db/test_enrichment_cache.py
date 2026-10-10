import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from db.enrichment_cache import SQLiteEnrichmentCache
from ingestion.cache import CacheBusyError


def test_existing_rows_remain_readable_and_expired_results_can_be_revalidated(tmp_path):
    path = tmp_path / "existing.db"
    result = {"outcome": "proposal", "source": {"sha256": "old-hash"}}
    # The pre-refactor schema and serialized result need no migration.
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE supplied_enrichment_cache (
            key TEXT PRIMARY KEY, expires REAL NOT NULL, lease_until REAL NOT NULL,
            result TEXT)""")
        conn.execute("INSERT INTO supplied_enrichment_cache VALUES (?, ?, ?, ?)",
                     ("key", 200, 0, json.dumps(result)))
    cache = SQLiteEnrichmentCache(path)
    assert cache.acquire("key", now=100, lease_until=400) == result
    assert cache.previous("key") == result
    assert cache.acquire("key", now=200, lease_until=500) is None
    assert cache.previous("key") is None
    restarted = SQLiteEnrichmentCache(path)
    with pytest.raises(CacheBusyError):
        restarted.acquire("key", now=499, lease_until=799, refresh=True)
    assert restarted.acquire("key", now=500, lease_until=800) is None
    restarted.save("key", result, expires=1000)
    assert cache.acquire("key", now=800, lease_until=1100) == result


def test_refresh_claim_clears_previous_and_save_only_updates_claimed_keys(tmp_path):
    cache = SQLiteEnrichmentCache(tmp_path / "cache.db")
    result = {"outcome": "no_match"}
    cache.save("unclaimed", result, expires=1000)
    assert cache.previous("unclaimed") is None
    cache.acquire("key", now=100, lease_until=400)
    cache.save("key", result, expires=1000)
    assert cache.previous("key") == result
    assert cache.acquire("key", now=200, lease_until=500, refresh=True) is None
    assert cache.previous("key") is None
    with pytest.raises(CacheBusyError):
        cache.acquire("key", now=200, lease_until=500)


def test_independent_connections_atomically_claim_same_key(tmp_path):
    path = tmp_path / "cache.db"
    caches = [SQLiteEnrichmentCache(path), SQLiteEnrichmentCache(path)]
    barrier = Barrier(2)

    def claim(cache):
        barrier.wait(timeout=5)
        try:
            assert cache.acquire("key", now=100, lease_until=400) is None
            return "claimed"
        except CacheBusyError:
            return "busy"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, caches)) == ["busy", "claimed"]
