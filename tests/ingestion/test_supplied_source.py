import copy
import json
import sqlite3
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from db.enrichment_cache import SQLiteEnrichmentCache
from db.repository import SQLitePartsBinRepository

from domain import AddPartRequest, AddStockRequest, ApplyReviewRequest, GetPartRequest, PartFields, PartsBinService, UpdatePartRequest
from domain.errors import DomainError
from ingestion import supplied_source as source

URL = "https://assets.nexperia.com/documents/data-sheet/PBSS5350T.pdf"
PAGE = "Nexperia PBSS5350T PNP low saturation transistor in SOT23."
DOCUMENT = source.Document(URL, "abc123", "2026-10-10T00:00:00+00:00", (PAGE,))


def candidate():
    return {"outcome": "proposal", "clarification": None, "mismatch_evidence": None, "fields": {
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


@pytest.mark.parametrize('url', [
    'https://www.st.com/resource/en/datasheet/bd139.pdf',
    'https://www.analog.com/media/en/technical-documentation/data-sheets/ad620.pdf',
    'https://ww1.microchip.com/downloads/en/DeviceDoc/MCP6001-1R-1U-2-4-1-MHz-Low-Power-Op-Amp-DS20001733L.pdf',
    'https://www.infineon.com/assets/row/public/documents/24/49/infineon-irf120-datasheet-en.pdf',
    'https://www.nxp.com/docs/en/data-sheet/PCF8574_PCF8574A.pdf',
    'https://www.diodes.com/datasheet/download/1N4148.pdf',
    'https://fscdn.rohm.com/en/products/databook/explanation/discrete/transistor/common/transistor_part_number_information_an-e.pdf',
    'https://product.tdk.com/en/system/files/dam/doc/product/emc/emc/power-line/catalog/pan_en.pdf',
])
def test_manufacturer_document_is_allowed(url):
    assert str(source.checked_url(url)) == url


@pytest.mark.parametrize('host', [
    'www.st.com', 'www.analog.com', 'ww1.microchip.com', 'www.infineon.com',
    'www.nxp.com', 'www.diodes.com', 'fscdn.rohm.com', 'product.tdk.com',
])
def test_manufacturer_lookalike_host_is_rejected(host):
    with pytest.raises(source.EnrichmentError, match='host is not supported'):
        source.checked_url(f'https://{host}.evil.example/datasheet.pdf')


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
    cache = SQLiteEnrichmentCache(path)
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


async def test_provider_failure_is_not_cached_as_no_match(tmp_path):
    cache = SQLiteEnrichmentCache(tmp_path / "cache.db")
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
                patch.object(source, "parse_pdf_bounded", return_value=((PAGE,), (), False)):
            document = await source.retrieve_pdf(URL, client)
            assert document.pages == (PAGE,)
            assert len(document.sha256) == 64
            with patch.object(source, "MAX_BYTES", 4):
                with pytest.raises(source.EnrichmentError):
                    await source.retrieve_pdf(URL, client)


def test_bad_pdf_is_rejected_by_isolated_parser():
    with pytest.raises(source.EnrichmentError):
        source.parse_pdf_bounded(b"not a PDF")


def test_pdf_table_lines_keep_variant_and_rating_on_same_row():
    from pdfminer.layout import LTAnno, LTContainer, LTPage, LTTextBoxHorizontal, LTTextLineHorizontal

    layout = LTPage(1, (0, 0, 300, 300))
    for x, cells in [(0, [('Variant', 200), ('TYPE-A', 180), ('TYPE-B', 160)]),
                     (100, [('Power at 25 °C', 200), ('0.25 W', 180), ('0.125 W', 160)])]:
        column = LTTextBoxHorizontal()
        for text, y in cells:
            line = LTTextLineHorizontal(0.1)
            LTContainer.add(line, LTAnno(text))
            line.set_bbox((x, y, x + 80, y + 10))
            column.add(line)
        layout.add(column)
    assert source.page_text(layout) == (
        'Variant\tPower at 25 °C\nTYPE-A\t0.25 W\nTYPE-B\t0.125 W\n')


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
    cache = SQLiteEnrichmentCache(tmp_path / "parts.db")
    cache.acquire("disposable", now=100, lease_until=400)
    cache.save("disposable", extraction(), expires=1000)
    from domain.models import ProvenanceRequest
    before = service.provenance(ProvenanceRequest(original.id))
    with sqlite3.connect(cache.path) as conn:
        conn.execute("DROP TABLE supplied_enrichment_cache")
    assert service.get(GetPartRequest(original.id)) == accepted
    assert service.provenance(ProvenanceRequest(original.id)) == before
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


async def test_enrichment_uses_injected_storage_contract():
    from unittest.mock import Mock
    from ingestion.cache import EnrichmentCache

    cache = Mock(spec=EnrichmentCache)
    cache.previous.return_value = None
    cache.acquire.return_value = None
    with patch.object(source.time, "time", return_value=100), \
            patch.object(source, "retrieve_pdf", AsyncMock(return_value=DOCUMENT)), \
            patch.object(source, "extract", AsyncMock(return_value=extraction())):
        result = await source.enrich("PBSS5350T", None, URL, api_key="fake",
                                     model="explicit-model", cache=cache)
    key = cache.previous.call_args.args[0]
    cache.acquire.assert_called_once_with(key, now=100, lease_until=400, refresh=False)
    cache.save.assert_called_once_with(key, extraction(), expires=100 + source.CACHE_SECONDS)
    assert result["cache_hit"] is False


@pytest.mark.parametrize("outcome,ttl", [
    ("proposal", source.CACHE_SECONDS), ("no_match", 86400), ("needs_clarification", 86400),
])
async def test_expiry_revalidates_hash_without_repeating_paid_extraction(tmp_path, outcome, ttl):
    cache = SQLiteEnrichmentCache(tmp_path / "cache.db")
    result = extraction() if outcome == "proposal" else {
        **extraction(), "outcome": outcome, "fields": {}, "clarification": "Which variant?"}
    later = source.Document(URL, DOCUMENT.sha256, "2026-10-11T00:00:00+00:00", (PAGE,))
    with patch.object(source.time, "time", return_value=100) as now, \
            patch.object(source, "retrieve_pdf", AsyncMock(return_value=DOCUMENT)) as retrieve, \
            patch.object(source, "extract", AsyncMock(return_value=result)) as extract:
        await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache)
        with sqlite3.connect(cache.path) as conn:
            assert conn.execute("SELECT expires FROM supplied_enrichment_cache").fetchone()[0] == 100 + ttl
        now.return_value = 100 + ttl - 1
        await source.enrich("PBSS5350T", None, URL, api_key="", model="explicit-model", cache=cache)
        assert retrieve.await_count == 1
        now.return_value = 100 + ttl
        retrieve.return_value = later
        refreshed = await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model",
                                        cache=SQLiteEnrichmentCache(cache.path))
        assert refreshed["cache_hit"] is True
        assert refreshed["source"]["retrieved_at"] == later.retrieved_at
        assert retrieve.await_count == 2
        assert extract.await_count == 1


@pytest.mark.parametrize("failure_stage", ["retrieve_pdf", "extract"])
async def test_failed_refresh_requires_explicit_retry_after_cooldown(tmp_path, failure_stage):
    cache = SQLiteEnrichmentCache(tmp_path / "cache.db")
    with patch.object(source.time, "time", return_value=100) as now, \
            patch.object(source, "retrieve_pdf", AsyncMock(return_value=DOCUMENT)) as retrieve, \
            patch.object(source, "extract", AsyncMock(return_value=extraction())) as extract:
        await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache)
        failed = retrieve if failure_stage == "retrieve_pdf" else extract
        retrieve.return_value = source.Document(URL, "changed", DOCUMENT.retrieved_at, (PAGE,))
        failed.side_effect = TimeoutError
        with pytest.raises(TimeoutError):
            await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model",
                                cache=cache, refresh=True)
        counts = (retrieve.await_count, extract.await_count)
        now.return_value = 399
        with pytest.raises(source.EnrichmentError, match="cooling down"):
            await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model",
                                cache=SQLiteEnrichmentCache(cache.path), refresh=True)
        assert (retrieve.await_count, extract.await_count) == counts
        now.return_value = 400
        assert (retrieve.await_count, extract.await_count) == counts
        failed.side_effect = None
        await source.enrich("PBSS5350T", None, URL, api_key="fake", model="explicit-model", cache=cache)
        assert retrieve.await_count == counts[0] + 1
        assert extract.await_count == counts[1] + 1


async def test_cache_key_preserves_identity_source_model_and_policy(tmp_path):
    cache = SQLiteEnrichmentCache(tmp_path / "cache.db")
    with patch.object(source, "retrieve_pdf", AsyncMock(return_value=DOCUMENT)) as retrieve, \
            patch.object(source, "extract", AsyncMock(return_value=extraction())) as extract:
        async def lookup(number="PBSS5350T", manufacturer=None, url=URL, model="explicit-model"):
            return await source.enrich(number, manufacturer, url, api_key="fake", model=model, cache=cache)

        await lookup()
        assert (await lookup(number=" pbss5350t "))["cache_hit"] is True
        await lookup(number="PBSS5350T,215")
        await lookup(manufacturer="Nexperia")
        assert (await lookup(manufacturer=" nexperia "))["cache_hit"] is True
        await lookup(url=URL + "?revision=2")
        await lookup(model="other-model")
        with patch.object(source, "POLICY_VERSION", "next-policy"):
            await lookup()
        assert retrieve.await_count == extract.await_count == 6


def test_excerpts_bound_multibyte_text_and_retain_late_exact_identity():
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (
        '🪿' * 6000,
        ('Other identity PBSS5350TX. ' * 250),
        PAGE,
    ))
    excerpts, omitted = source.select_excerpts(document, 'PBSS5350T')
    assert omitted
    assert sum(len(page['text'].encode('utf-8')) for page in excerpts) <= source.MAX_EXCERPT_BYTES
    assert {'page': 3, 'text': PAGE} in excerpts
    for page in excerpts:
        assert page['text'] in document.pages[page['page'] - 1]
    assert source.select_excerpts(DOCUMENT, 'PBSS5350T') == ([{'page': 1, 'text': PAGE}], False)


async def test_selected_excerpts_use_original_citations_and_validate_visible_evidence():
    captured = []
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, ('x' * 20_000, PAGE))
    result = candidate()
    for field in result['fields'].values():
        field['evidence']['page'] = 2
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extracted = await source.extract(document, 'PBSS5350T', None, api_key='fake', model='test', client=client)
    model_input = json.loads(captured[0]['input'])
    assert model_input['text_omitted'] is True
    assert sum(len(page['text'].encode('utf-8')) for page in model_input['pages']) <= source.MAX_EXCERPT_BYTES
    assert extracted['fields']['part_number']['evidence']['page'] == 2


@pytest.mark.parametrize('text', ['x' * 20_000, 'Unresolved family identity'])
async def test_excerpt_absence_does_not_become_no_match(text):
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (text,))
    outcome = {'outcome': 'no_match', 'clarification': None, 'mismatch_evidence': None, 'fields': dict.fromkeys(source.FIELDS)}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'status': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(outcome)}]}],
    }))) as client:
        result = await source.extract(document, 'PBSS5350T', None, api_key='fake', model='test', client=client)
    assert result['outcome'] == 'needs_clarification'
    assert result['fields'] == {}


async def test_cannot_quote_evidence_outside_selected_excerpts():
    hidden = 'Secret package evidence'
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (PAGE + hidden,))
    result = candidate()
    result['fields']['package']['evidence']['excerpt'] = hidden
    with patch.object(source, 'select_excerpts', return_value=([{'page': 1, 'text': PAGE}], True)):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                'status': 'completed', 'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': json.dumps(result)}]}],
        }))) as client:
            with pytest.raises(source.EnrichmentError, match='supplied excerpts'):
                await source.extract(document, 'PBSS5350T', None, api_key='fake', model='test', client=client)


@pytest.mark.parametrize('omitted', [False, True])
async def test_wrong_document_requires_visible_mismatch_evidence(omitted):
    passage = 'Coilcraft TA7618 flyback transformer'
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (passage,))
    raw = {'outcome': 'no_match', 'clarification': None,
           'mismatch_evidence': {'page': 1, 'excerpt': passage},
           'fields': dict.fromkeys(source.FIELDS)}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(raw)}]}],
    }))) as client:
        with patch.object(source, 'select_excerpts', return_value=([{'page': 1, 'text': passage}], omitted)):
            result = await source.extract(document, 'NE555P', 'Texas Instruments',
                                          api_key='fake', model='test', client=client)
    assert result['outcome'] == 'no_match'
    assert result['fields'] == {}
    assert result['mismatch_evidence'] == raw['mismatch_evidence']


async def test_mismatch_cannot_cite_hidden_text():
    visible = 'Family identity is unresolved.'
    hidden = 'Coilcraft TA7618 flyback transformer'
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (visible + '\n' + hidden,))
    raw = {'outcome': 'no_match', 'clarification': None,
           'mismatch_evidence': {'page': 1, 'excerpt': hidden},
           'fields': dict.fromkeys(source.FIELDS)}
    with patch.object(source, 'select_excerpts', return_value=([{'page': 1, 'text': visible}], True)):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'status': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(raw)}]}],
        }))) as client:
            with pytest.raises(source.EnrichmentError, match='supplied excerpts'):
                await source.extract(document, 'NE555P', None, api_key='fake', model='test', client=client)


@pytest.mark.parametrize('evidence', [
    {'page': True, 'excerpt': PAGE}, {'page': 2, 'excerpt': PAGE},
    {'page': 1, 'excerpt': 'Invented transformer'}, {'page': 1, 'excerpt': ''},
])
def test_mismatch_rejects_invalid_evidence(evidence):
    raw = {'outcome': 'no_match', 'clarification': None,
           'mismatch_evidence': evidence, 'fields': dict.fromkeys(source.FIELDS)}
    with pytest.raises(source.EnrichmentError):
        source.validate_candidate(raw, 'NE555P', None, DOCUMENT)


async def test_exact_timer_ordering_row_supports_reviewed_package():
    row = 'NE555P\tActive\tProduction\tPDIP (P) | 8\t50 | TUBE\n'
    header = 'PACKAGE OPTION ADDENDUM\nOrderable part number\tStatus\tMaterial type\tPackage | Pins\n'
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (
        'Texas Instruments precision timers.\n' + 'other text\n' * 1000,
        header + 'Other ordering rows\n' * 100 + row + 'NE555PS\tSO (PS) | 8\n',
    ))
    raw = {'outcome': 'proposal', 'clarification': None, 'mismatch_evidence': None, 'fields': {
        'manufacturer': {'value': 'Texas Instruments', 'evidence': {'page': 1, 'excerpt': 'Texas Instruments'}},
        'part_number': {'value': 'NE555P', 'evidence': {'page': 2, 'excerpt': row.strip()}},
        'package': {'value': 'PDIP-8', 'evidence': {'page': 2, 'excerpt': row.strip()}},
        'description': None,
    }}
    def handler(request):
        pages = json.loads(json.loads(request.content)['input'])['pages']
        assert any(item['page'] == 2 and header in item['text'] for item in pages)
        assert any(item['page'] == 2 and row in item['text'] for item in pages)
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(raw)}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await source.extract(document, 'NE555P', 'Texas Instruments', api_key='fake', model='test', client=client)
    review = source.review_result(result)
    assert review['chosen_updates'] == {'manufacturer': 'Texas Instruments', 'part_number': 'NE555P', 'package': 'PDIP-8'}
    package = next(item for item in review['durable_provenance'] if item['field_name'] == 'package')
    assert json.loads(package['evidence'])['excerpt'] == row.strip()


@pytest.mark.parametrize('variant', ['NE555P.A', 'NE555PE4', 'NE555PS', 'NE555P,123', 'NE555P/123'])
def test_timer_identity_cannot_drop_ordering_suffix(variant):
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (f'Texas Instruments {variant}',))
    raw = {'outcome': 'proposal', 'clarification': None, 'mismatch_evidence': None, 'fields': {
        'manufacturer': {'value': 'Texas Instruments', 'evidence': {'page': 1, 'excerpt': 'Texas Instruments'}},
        'part_number': {'value': 'NE555P', 'evidence': {'page': 1, 'excerpt': variant}},
        'package': None, 'description': None,
    }}
    with pytest.raises(source.EnrichmentError, match='exact ordering variant'):
        source.validate_candidate(raw, 'NE555P', 'Texas Instruments', document)


def test_description_cannot_join_words_across_interleaved_columns():
    document = source.Document(URL, 'hash', DOCUMENT.retrieved_at, (
        PAGE + '\nFeature bullet\tThese devices are precision timing\nAnother bullet\tcircuits for delays.\n',))
    raw = candidate()
    raw['fields']['description'] = {'value': 'Precision timing circuit', 'evidence': {
        'page': 1, 'excerpt': 'precision timing circuits'}}
    with pytest.raises(source.EnrichmentError, match='not present on the cited page'):
        source.validate_candidate(raw, 'PBSS5350T', 'Nexperia', document)
