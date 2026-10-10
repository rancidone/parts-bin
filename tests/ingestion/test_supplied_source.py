import copy
import json
import sqlite3
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from db.repository import SQLitePartsBinRepository

from domain import AddPartRequest, AddStockRequest, ApplyReviewRequest, GetPartRequest, PartFields, PartsBinService, UpdatePartRequest
from domain.errors import DomainError
from ingestion import supplied_source as source

URL = "https://assets.nexperia.com/documents/data-sheet/PBSS5350T.pdf"
PAGE = "Nexperia PBSS5350T PNP low saturation transistor in SOT23."
DOCUMENT = source.Document(URL, "abc123", "2026-10-10T00:00:00+00:00", (PAGE,))


def candidate():
    return {"outcome": "proposal", "clarification": None, "fields": {
        name: {"value": value, "evidence": {"page": 1, "excerpt": excerpt}}
        for name, value, excerpt in [
            ("manufacturer", "Nexperia", "Nexperia"),
            ("part_number", "PBSS5350T", "PBSS5350T"),
            ("package", "SOT23", "in SOT23"),
            ("description", "PNP low saturation transistor", "PNP low saturation transistor"),
        ]}}


def extraction():
    validated = source.validate_candidate(candidate(), "PBSS5350T", None, DOCUMENT)
    return {**validated, "source": {"url": URL, "sha256": DOCUMENT.sha256,
            "retrieved_at": DOCUMENT.retrieved_at}, "model": "explicit-model",
            "policy_version": source.POLICY_VERSION, "usage": {"input_tokens": 100, "output_tokens": 50}}


@pytest.mark.parametrize("url", ["http://assets.nexperia.com/a.pdf", "https://127.0.0.1/a.pdf",
    "https://assets.nexperia.com.evil.example/a.pdf", "https://user:password@assets.nexperia.com/a.pdf",
    "https://assets.nexperia.com:8000/a.pdf", "file:///tmp/a.pdf"])
def test_reject_unapproved_urls(url):
    with pytest.raises(source.EnrichmentError):
        source.checked_url(url)


@pytest.mark.parametrize("change", ["identity", "manufacturer", "quote", "page", "quantity", "empty"])
def test_reject_untrustworthy_candidate(change):
    result = candidate()
    if change == "identity":
        result["fields"]["part_number"]["value"] = "PBSS5350X"
    elif change == "manufacturer":
        result["fields"]["manufacturer"]["value"] = "Other manufacturer"
    elif change == "quote":
        result["fields"]["package"]["evidence"]["excerpt"] = "Invented quotation"
    elif change == "page":
        result["fields"]["package"]["evidence"]["page"] = True
    elif change == "quantity":
        result["fields"]["quantity"] = 10
    else:
        result["fields"]["description"]["value"] = " "
    with pytest.raises(source.EnrichmentError):
        source.validate_candidate(result, "PBSS5350T", "Nexperia", DOCUMENT)


def test_unknown_fields_stay_unknown_and_ambiguity_cannot_propose():
    result = candidate()
    result["fields"]["package"] = None
    validated = source.validate_candidate(result, "PBSS5350T", None, DOCUMENT)
    assert "package" not in validated["fields"]
    result["outcome"] = "needs_clarification"
    with pytest.raises(source.EnrichmentError):
        source.validate_candidate(result, "PBSS5350T", None, DOCUMENT)
    result["fields"] = dict.fromkeys(source.FIELDS)
    result["clarification"] = "Which manufacturer?"
    assert source.validate_candidate(result, "PBSS5350T", None, DOCUMENT)["fields"] == {}


async def test_extraction_has_no_tools_and_rejects_incomplete_output():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "incomplete", "output": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(source.EnrichmentError):
            await source.extract(DOCUMENT, "PBSS5350T", None, api_key="fake", model="explicit-model", client=client)
    assert len(requests) == 1
    assert requests[0]["store"] is False
    assert requests[0]["tools"] == []
    assert requests[0]["max_output_tokens"] == 2000
    assert requests[0]["text"]["format"]["strict"] is True


async def test_extraction_returns_only_validated_fields_and_usage():
    def handler(request):
        return httpx.Response(200, json={"status": "completed", "usage": {"input_tokens": 100},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(candidate())}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await source.extract(DOCUMENT, "PBSS5350T", None, api_key="fake", model="explicit-model", client=client)
    assert result["source"]["sha256"] == DOCUMENT.sha256
    assert result["fields"]["package"]["evidence"]["source_url"] == URL
    assert result["usage"]["input_tokens"] == 100


async def test_cache_reuses_result_without_retrieval_and_separates_identity(tmp_path):
    path = tmp_path / "cache.db"
    cache = source.ResultCache(path)
    with patch.object(source, "retrieve_pdf", AsyncMock(return_value=DOCUMENT)) as retrieve, \
            patch.object(source, "extract", AsyncMock(return_value=extraction())) as extract:
        first = await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache)
        second = await source.enrich("PBSS5350T", None, URL, api_key="", model="explicit-model", cache=cache)
        assert first["cache_hit"] is False and second["cache_hit"] is True
        assert retrieve.await_count == extract.await_count == 1
        await source.enrich("PBSS5350T", "Nexperia", URL, api_key="fake", model="explicit-model", cache=cache)
        assert extract.await_count == 2
        await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache, refresh=True)
        assert extract.await_count == 2
        assert retrieve.await_count == 3
        retrieve.return_value = source.Document(URL, "changed-hash", DOCUMENT.retrieved_at, (PAGE,))
        await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache, refresh=True)
        assert extract.await_count == 3
    with sqlite3.connect(path) as conn:
        stored = json.loads(conn.execute("SELECT result FROM supplied_enrichment_cache LIMIT 1").fetchone()[0])
    assert "pages" not in stored and "api_key" not in stored


def test_cache_lease_and_expiry(tmp_path):
    cache = source.ResultCache(tmp_path / "cache.db")
    with patch.object(source.time, "time", return_value=100):
        assert cache.acquire("key") is None
        with pytest.raises(source.EnrichmentError):
            source.ResultCache(cache.path).acquire("key")
        cache.save("key", extraction())
        assert cache.acquire("key")["outcome"] == "proposal"
    with patch.object(source.time, "time", return_value=100 + source.CACHE_SECONDS + 1):
        assert cache.acquire("key") is None


async def test_provider_failure_is_not_cached_as_no_match(tmp_path):
    cache = source.ResultCache(tmp_path / "cache.db")
    with patch.object(source, "retrieve_pdf", AsyncMock(side_effect=TimeoutError)):
        with pytest.raises(TimeoutError):
            await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache)
    with sqlite3.connect(cache.path) as conn:
        assert conn.execute("SELECT result FROM supplied_enrichment_cache").fetchone()[0] is None
    with pytest.raises(source.EnrichmentError):
        await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache)


async def test_retrieval_blocks_private_resolution_and_redirects():
    with patch.object(source.socket, "getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]):
        with pytest.raises(source.EnrichmentError):
            await source.public_destination("assets.nexperia.com")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(302, headers={"location": "https://127.0.0.1/internal"}))) as client:
        with patch.object(source, "public_destination", AsyncMock()):
            with pytest.raises(source.EnrichmentError):
                await source.retrieve_pdf(URL, client)


async def test_retrieval_limits_bytes_and_keeps_document_transient():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, content=b"%PDF-fake"))) as client:
        with patch.object(source, "public_destination", AsyncMock()), \
                patch.object(source, "parse_pdf_bounded", return_value=(PAGE,)):
            document = await source.retrieve_pdf(URL, client)
            assert document.pages == (PAGE,)
            assert len(document.sha256) == 64
            with patch.object(source, "MAX_BYTES", 4):
                with pytest.raises(source.EnrichmentError):
                    await source.retrieve_pdf(URL, client)


def test_bad_pdf_is_rejected_by_isolated_parser():
    with pytest.raises(source.EnrichmentError):
        source.parse_pdf_bounded(b"not a PDF")


def part(service):
    return service.add_part(AddPartRequest(PartFields(part_category="transistor", profile="discrete_ic",
        quantity=7, part_number="PBSS5350T")))


def test_stage_and_accept_keep_quantity_and_evidence(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"))
    original = part(service)
    result = source.review_result(extraction())
    service.add_stock(AddStockRequest(original.id, 2))
    service.stage_enrichment(original, result["chosen_updates"], result["durable_provenance"])
    assert service.get(GetPartRequest(original.id)).package is None
    assert service.get(GetPartRequest(original.id)).quantity == 9
    accepted = service.apply_review(ApplyReviewRequest(original.id))
    assert accepted.package == "SOT23" and accepted.quantity == 9
    from domain.models import ProvenanceRequest
    evidence = service.provenance(ProvenanceRequest(original.id))
    assert json.loads(evidence[0]["evidence"])["sha256"] == DOCUMENT.sha256


def test_staging_rejects_changed_metadata_and_preserves_pending_review(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"))
    original = part(service)
    result = source.review_result(extraction())
    service.update_part(UpdatePartRequest(original.id, {"package": "user-package"}))
    with pytest.raises(DomainError):
        service.stage_enrichment(original, result["chosen_updates"], result["durable_provenance"])
    current = service.get(GetPartRequest(original.id))
    service.stage_enrichment(current, result["chosen_updates"], result["durable_provenance"])
    before = copy.deepcopy(service.list_pending_reviews())
    with pytest.raises(DomainError):
        service.stage_enrichment(current, result["chosen_updates"], result["durable_provenance"])
    assert service.list_pending_reviews() == before


def test_staging_rejects_quantity_and_unevidenced_fields(tmp_path):
    service = PartsBinService(SQLitePartsBinRepository(tmp_path / "parts.db"))
    original = part(service)
    with pytest.raises(DomainError):
        service.stage_enrichment(original, {"quantity": 100}, [])
    with pytest.raises(DomainError):
        service.stage_enrichment(original, {"package": "SOT23"}, [])
    assert service.list_pending_reviews() == {}
