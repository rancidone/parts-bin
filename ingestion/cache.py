"""Disposable enrichment storage, independent of authoritative repositories."""

from typing import Protocol

from ingestion.errors import EnrichmentError


class CacheBusyError(EnrichmentError):
    """A lookup is active or within its failure cooldown."""


class EnrichmentCache(Protocol):
    """Store validated compact results only, never documents or photos.

    Times are absolute Unix timestamps supplied by the enrichment policy. Loss
    of this storage must not affect inventory, accepted evidence, or retry IDs.
    """

    def previous(self, key: str) -> dict | None:
        """Read a saved result even when expired, for document-hash revalidation."""
        ...

    def acquire(self, key: str, *, now: float, lease_until: float,
                refresh: bool = False) -> dict | None:
        """Atomically reject an active lease, return a fresh hit, or claim work.

        Active leases raise CacheBusyError, including on refresh. A claim clears
        the prior result and freshness and returns None. The lease remains on
        failure/interruption; only a later explicit acquire may retry after it
        expires. This is a cooldown, not a durable queue or paid-call guarantee.
        """
        ...

    def save(self, key: str, result: dict, *, expires: float) -> None:
        """Save the claimed result and clear its lease; do not create a new key."""
        ...
