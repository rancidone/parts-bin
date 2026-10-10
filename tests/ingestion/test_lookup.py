"""Tests for spec lookup merge logic (no HTTP calls)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ingestion.lookup import _digikey_debug_summary, _http_error_details, fetch_specs_detailed, merge_specs


class TestMergeSpecs:
    def test_fills_manufacturer_and_description(self):
        record = {"package": "SOT-23", "manufacturer": None, "description": None}
        specs = {"manufacturer": "Nexperia", "description": "N-ch MOSFET"}
        merged = merge_specs(record, specs)
        assert merged["manufacturer"] == "Nexperia"
        assert merged["description"] == "N-ch MOSFET"

    def test_fills_package_when_null(self):
        record = {"package": None, "manufacturer": None, "description": None}
        specs = {"package": "SOT-23"}
        merged = merge_specs(record, specs)
        assert merged["package"] == "SOT-23"

    def test_overwrites_existing_package(self):
        record = {"package": "SOT-323", "manufacturer": None, "description": None}
        specs = {"package": "SOT-23"}
        merged = merge_specs(record, specs)
        assert merged["package"] == "SOT-23"

    def test_empty_specs_leaves_record_unchanged(self):
        record = {"package": "0402", "manufacturer": None, "description": None}
        merged = merge_specs(record, {})
        assert merged == record

    def test_original_record_not_mutated(self):
        record = {"package": None, "manufacturer": None, "description": None}
        merge_specs(record, {"package": "SOT-23"})
        assert record["package"] is None


class TestLookupResolution:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code, outcome", [
        (404, "no_match"), (401, "failed"), (403, "failed"),
        (429, "failed"), (500, "failed"), (503, "failed"),
    ])
    async def test_product_details_http_outcomes(self, status_code, outcome):
        original_async_client = httpx.AsyncClient
        requests = []

        async def handler(request):
            requests.append(request)
            if request.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "test-token"})
            if request.url.path == "/products/v4/search/keyword":
                return httpx.Response(200, json={"Products": [], "ProductsCount": 0})
            return httpx.Response(status_code, json={"status": status_code})

        with patch("ingestion.lookup.httpx.AsyncClient", side_effect=lambda *args, **kwargs:
                   original_async_client(transport=httpx.MockTransport(handler))):
            result = await fetch_specs_detailed("LM386", {
                "client_id": "id", "client_secret": "secret",
            })

        assert result["outcome"] == outcome
        assert result["chosen_updates"] == {}
        assert result["durable_provenance"] == []
        assert len(requests) == (3 if status_code == 404 else 2)
        attempt = result["source_attempts"][0]
        assert attempt["status"] == outcome
        if outcome == "failed":
            assert attempt["error"]["status_code"] == status_code

    @pytest.mark.asyncio
    async def test_product_not_found_allows_configured_search(self):
        original_async_client = httpx.AsyncClient

        async def handler(request):
            if request.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "test-token"})
            if request.url.path == "/products/v4/search/keyword":
                return httpx.Response(200, json={"Products": [], "ProductsCount": 0})
            return httpx.Response(404)

        with patch("ingestion.lookup.httpx.AsyncClient", side_effect=lambda *args, **kwargs:
                   original_async_client(transport=httpx.MockTransport(handler))), patch(
                       "ingestion.lookup.search_datasheet_pdfs", AsyncMock(return_value=[])) as search:
            result = await fetch_specs_detailed("LM386", {
                "client_id": "id", "client_secret": "secret",
            }, search_config={})

        search.assert_awaited_once()
        assert search.await_args.args[0] == "LM386"
        assert result["outcome"] == "no_match"
        assert result["chosen_updates"] == {}

    @pytest.mark.asyncio
    async def test_family_search_requires_clarification_without_staging_variant_fields(self):
        original_async_client = httpx.AsyncClient
        products = [
            {"ManufacturerProductNumber": number, "Manufacturer": {"Name": manufacturer},
             "ProductUrl": f"https://www.digikey.com/{number}"}
            for number, manufacturer in [("LM386N-1/NOPB", "Texas Instruments"),
                                         ("LM386N-4/NOPB", "Texas Instruments"),
                                         ("SS3P6L-M3/86A", "Vishay")]
        ]

        async def handler(request):
            if request.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "test-token"})
            if request.url.path == "/products/v4/search/keyword":
                import json
                assert json.loads(request.content) == {"Keywords": "LM386", "Limit": 5, "Offset": 0}
                return httpx.Response(200, json={"Products": products, "ProductsCount": 34})
            return httpx.Response(404)

        with patch("ingestion.lookup.httpx.AsyncClient", side_effect=lambda *args, **kwargs:
                   original_async_client(transport=httpx.MockTransport(handler))), patch(
                       "ingestion.lookup.search_datasheet_pdfs", AsyncMock()) as search:
            result = await fetch_specs_detailed("LM386", {
                "client_id": "id", "client_secret": "secret",
            }, search_config={})

        assert result["outcome"] == "needs_clarification"
        assert result["requires_confirmation"] is True
        assert result["chosen_updates"] == {}
        assert result["durable_provenance"] == []
        assert result["candidate_count"] == 34
        assert [candidate["part_number"] for candidate in result["lookup_candidates"]] == [
            product["ManufacturerProductNumber"] for product in products]
        search.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
    async def test_candidate_search_failure_remains_failure(self, status_code):
        original_async_client = httpx.AsyncClient

        async def handler(request):
            if request.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "test-token"})
            if request.url.path == "/products/v4/search/keyword":
                return httpx.Response(status_code)
            return httpx.Response(404)

        with patch("ingestion.lookup.httpx.AsyncClient", side_effect=lambda *args, **kwargs:
                   original_async_client(transport=httpx.MockTransport(handler))):
            result = await fetch_specs_detailed("LM386", {
                "client_id": "id", "client_secret": "secret",
            })

        assert result["outcome"] == "failed"
        assert result["lookup_candidates"] == []
        assert result["source_attempts"][0]["error"]["status_code"] == status_code

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_no_credentials_returns_no_match(self):
        result = await fetch_specs_detailed("TLV62565DBVR", digikey_credentials=None)

        assert result["chosen_updates"] == {}
        assert result["provider"] is None
        assert result["tried_providers"] == []
        assert result["outcome"] == "no_match"

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_digikey_match_returns_saved(self):
        with patch("ingestion.lookup._digikey_lookup_detailed", AsyncMock(return_value={
            "specs": {
                "part_number": "TLV62565DBVR",
                "manufacturer": "Texas Instruments",
                "description": "Buck Switching Regulator IC",
            },
            "debug": {
                "requested_part_number": "TLV62565DBVR",
                "manufacturer_part_number": "TLV62565DBVR",
            },
            "status": "ok",
        })):
            result = await fetch_specs_detailed("TLV62565DBVR", {
                "client_id": "id",
                "client_secret": "secret",
            })

        assert result["provider"] == "digikey"
        assert result["chosen_updates"]["manufacturer"] == "Texas Instruments"
        assert result["tried_providers"] == ["digikey"]
        assert result["outcome"] == "saved"

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_uses_api_derived_page_to_fill_missing_field(self):
        original_async_client = httpx.AsyncClient

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/product":
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text="""
                    <html>
                      <head>
                        <script type="application/ld+json">
                          {
                            "@type": "Product",
                            "mpn": "TLV62565DBVR",
                            "manufacturer": {"name": "Texas Instruments"},
                            "additionalProperty": [
                              {"name": "Package / Case", "value": "SOT-23-5"}
                            ]
                          }
                        </script>
                      </head>
                    </html>
                    """,
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        with patch(
            "ingestion.lookup.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: original_async_client(transport=transport),
        ):
            with patch("ingestion.lookup._digikey_lookup_detailed", AsyncMock(return_value={
                "specs": {
                    "part_number": "TLV62565DBVR",
                    "manufacturer": "Texas Instruments",
                },
                "debug": {"product_url": "https://example.com/product"},
                "status": "ok",
            })):
                result = await fetch_specs_detailed("TLV62565DBVR", {
                    "client_id": "id",
                    "client_secret": "secret",
                })

        assert result["outcome"] == "saved"
        assert result["chosen_updates"]["package"] == "SOT-23-5"
        assert any(
            attempt["authority_tier"] == "api_derived_page"
            for attempt in result["source_attempts"]
        )
        package_candidates = result["field_candidates"]["package"]
        assert package_candidates[0]["extraction_method"] in {
            "digikey-html-labeled-row",
            "json-ld-additional-property",
        }
        assert package_candidates[0]["evidence"]

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_uses_api_derived_pdf_to_fill_missing_field(self):
        original_async_client = httpx.AsyncClient

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/datasheet.pdf":
                return httpx.Response(
                    200,
                    headers={"content-type": "application/pdf"},
                    content=b"%PDF-1.4 Manufacturer: Texas Instruments Package / Case: SOT-23-5 endstream",
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        with patch(
            "ingestion.lookup.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: original_async_client(transport=transport),
        ):
            with patch("ingestion.lookup._digikey_lookup_detailed", AsyncMock(return_value={
                "specs": {
                    "part_number": "TLV62565DBVR",
                    "manufacturer": "Texas Instruments",
                },
                "debug": {"datasheet_url": "https://example.com/datasheet.pdf"},
                "status": "ok",
            })):
                result = await fetch_specs_detailed("TLV62565DBVR", {
                    "client_id": "id",
                    "client_secret": "secret",
                })

        assert result["outcome"] == "saved"
        assert result["chosen_updates"]["package"] == "SOT-23-5"
        assert any(
            attempt["authority_tier"] == "api_derived_pdf"
            for attempt in result["source_attempts"]
        )
        package_candidates = result["field_candidates"]["package"]
        assert package_candidates[0]["extraction_method"] == "pdf-labeled-text"
        assert "Package / Case" in package_candidates[0]["evidence"]

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_keeps_lower_tier_disagreement_in_provenance(self):
        original_async_client = httpx.AsyncClient

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/datasheet.pdf":
                return httpx.Response(
                    200,
                    headers={"content-type": "application/pdf"},
                    content=b"%PDF-1.4 Manufacturer: Different Vendor Package / Case: SOT-23-5 endstream",
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        with patch(
            "ingestion.lookup.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: original_async_client(transport=transport),
        ):
            with patch("ingestion.lookup._digikey_lookup_detailed", AsyncMock(return_value={
                "specs": {
                    "part_number": "TLV62565DBVR",
                    "manufacturer": "Texas Instruments",
                },
                "debug": {"datasheet_url": "https://example.com/datasheet.pdf"},
                "status": "ok",
            })):
                result = await fetch_specs_detailed("TLV62565DBVR", {
                    "client_id": "id",
                    "client_secret": "secret",
                })

        assert result["outcome"] == "saved"
        manufacturer_provenance = next(
            record for record in result["durable_provenance"]
            if record["field_name"] == "manufacturer"
        )
        assert manufacturer_provenance["field_value"] == "Texas Instruments"
        assert manufacturer_provenance["competing_candidates"] == [{
            "field_value": "Different Vendor",
            "source_tier": "api_derived_pdf",
            "source_kind": "pdf_document",
            "source_locator": "https://example.com/datasheet.pdf",
            "extraction_method": "pdf-labeled-text",
            "provider": "digikey",
            "evidence": "Manufacturer: Different Vendor",
            "conflict_status": "clear",
        }]

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_marks_description_merge_provenance(self):
        original_async_client = httpx.AsyncClient

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/datasheet.pdf":
                return httpx.Response(
                    200,
                    headers={"content-type": "application/pdf"},
                    content=b"%PDF-1.4 Description: Buck Switching Regulator IC with integrated power switch and 1.5A output current endstream",
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        with patch(
            "ingestion.lookup.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: original_async_client(transport=transport),
        ):
            with patch("ingestion.lookup._digikey_lookup_detailed", AsyncMock(return_value={
                "specs": {
                    "part_number": "TLV62565DBVR",
                    "manufacturer": "Texas Instruments",
                    "description": "Buck Switching Regulator IC",
                },
                "debug": {"datasheet_url": "https://example.com/datasheet.pdf"},
                "status": "ok",
            })):
                result = await fetch_specs_detailed("TLV62565DBVR", {
                    "client_id": "id",
                    "client_secret": "secret",
                })

        assert result["outcome"] == "saved"
        assert result["chosen_updates"]["description"] == (
            "Buck Switching Regulator IC with integrated power switch and 1.5A output current"
        )
        description_provenance = next(
            record for record in result["durable_provenance"]
            if record["field_name"] == "description"
        )
        assert description_provenance["normalization_method"] == "source_description_merge"
        assert description_provenance["extraction_method"] == "source-description-merge"
        assert description_provenance["competing_candidates"] == [{
            "field_value": "Buck Switching Regulator IC",
            "source_tier": "primary_api",
            "source_kind": "api",
            "source_locator": None,
            "extraction_method": "api",
            "provider": "digikey",
            "evidence": None,
            "conflict_status": "clear",
        }]

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_withholds_non_deterministic_taxonomy_fields(self):
        with patch("ingestion.lookup._digikey_lookup_detailed", AsyncMock(return_value={
            "specs": {
                "part_category": "buck_regulator",
                "profile": "discrete_ic",
                "value": "0.6V",
            },
            "debug": {
                "requested_part_number": "TLV62565DBVR",
                "manufacturer_part_number": "TLV62565DBVR",
            },
            "status": "ok",
        })):
            result = await fetch_specs_detailed("TLV62565DBVR", {
                "client_id": "id",
                "client_secret": "secret",
            })

        assert result["chosen_updates"] == {}
        assert result["outcome"] == "incomplete"
        assert set(result["withheld_candidates"]) == {"part_category", "profile", "value"}
        digikey_attempt = next(
            attempt for attempt in result["source_attempts"]
            if attempt["provider"] == "digikey"
        )
        assert "withheld_non_deterministic_field:part_category" in digikey_attempt["warnings"]
        assert "withheld_non_deterministic_field:profile" in digikey_attempt["warnings"]
        assert "withheld_non_deterministic_field:value" in digikey_attempt["warnings"]

    @pytest.mark.asyncio
    async def test_fetch_specs_detailed_suppresses_ambiguous_pdf_variant_fields(self):
        original_async_client = httpx.AsyncClient

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/datasheet.pdf":
                return httpx.Response(
                    200,
                    headers={"content-type": "application/pdf"},
                    content=b"""
                    %PDF-1.4
                    Manufacturer: Texas Instruments
                    MPN: TLV62565DBVR
                    Package / Case: SOT-23-5
                    Description: Buck Switching Regulator IC
                    MPN: TLV62566DBVR
                    Package / Case: WSON-6
                    Description: Buck Switching Regulator IC
                    endstream
                    """,
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        with patch(
            "ingestion.lookup.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: original_async_client(transport=transport),
        ):
            with patch("ingestion.lookup._digikey_lookup_detailed", AsyncMock(return_value={
                "specs": {
                    "part_number": "TLV62565DBVR",
                    "manufacturer": "Texas Instruments",
                },
                "debug": {"datasheet_url": "https://example.com/datasheet.pdf"},
                "status": "ok",
            })):
                result = await fetch_specs_detailed("TLV62565DBVR", {
                    "client_id": "id",
                    "client_secret": "secret",
                })

        assert result["outcome"] == "saved"
        assert "package" not in result["chosen_updates"]
        assert "description" not in result["chosen_updates"]
        pdf_attempt = next(
            attempt for attempt in result["source_attempts"]
            if attempt["authority_tier"] == "api_derived_pdf"
        )
        assert pdf_attempt["fields"] == {"manufacturer": "Texas Instruments"}
        assert "ambiguous_pdf_candidates:description,package,part_number" in pdf_attempt["warnings"]

    def test_digikey_debug_summary_extracts_identifying_fields(self):
        summary = _digikey_debug_summary({
            "Product": {
                "DigiKeyPartNumber": "296-12345-1-ND",
                "ManufacturerPartNumber": "TLV62565DBVR",
                "ProductUrl": "https://www.digikey.com/example",
                "ProductDescription": "Buck Switching Regulator IC",
                "DetailedDescription": "Positive Adjustable 0.6V 1 Output 1.5A",
                "Manufacturer": {"Name": "Texas Instruments"},
                "PackageType": {"Name": "SC-74A, SOT-753"},
                "Series": "Automotive, AEC-Q100",
            },
        }, "TLV62565DBVR")

        assert summary["requested_part_number"] == "TLV62565DBVR"
        assert summary["manufacturer_part_number"] == "TLV62565DBVR"
        assert summary["product_description"] == "Buck Switching Regulator IC"
        assert summary["package"] == "SC-74A, SOT-753"

    def test_http_error_details_includes_status_and_body(self):
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(403, request=request, text="forbidden")
        exc = httpx.HTTPStatusError("bad status", request=request, response=response)

        details = _http_error_details(exc)

        assert details["error_type"] == "HTTPStatusError"
        assert details["status_code"] == 403
        assert details["response_body"] == "forbidden"
