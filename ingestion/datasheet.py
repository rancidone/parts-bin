"""Configured supplied-source adapter; credentials never enter tool arguments."""

import httpx

from domain import DomainError, Part
from domain.errors import ErrorCode
from ingestion.cache import EnrichmentCache
from ingestion.supplied_source import EnrichmentError, checked_url, enrich


class DatasheetFetcher:
    def __init__(self, *, api_key: str, model: str, cache: EnrichmentCache, refresh: bool = False):
        self.api_key = api_key
        self.model = model
        self.cache = cache
        self.refresh = refresh

    async def __call__(self, part: Part, source_url: str) -> dict:
        try:
            checked_url(source_url)
        except EnrichmentError as exc:
            raise DomainError(ErrorCode.INVALID_INPUT, str(exc)) from exc
        except (ValueError, httpx.InvalidURL) as exc:
            raise DomainError(ErrorCode.INVALID_INPUT, 'The datasheet link is invalid. Update it in Edit part with an HTTPS manufacturer PDF link.') from exc
        try:
            return await enrich(part.part_number, part.manufacturer, source_url,
                                api_key=self.api_key, model=self.model, cache=self.cache,
                                category=part.part_category, refresh=self.refresh,
                                linked_part=part.datasheet_url == source_url)
        except EnrichmentError as exc:
            raise DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE, str(exc)) from exc
        except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
            # Provider failures may carry request URLs or credentials; expose only the class.
            raise DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE,
                              f'Datasheet extraction failed ({type(exc).__name__}); no review staged') from exc
