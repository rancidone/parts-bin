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

    def test_does_not_overwrite_user_package(self):
        record = {"package": "SOT-323", "manufacturer": None, "description": None}
        specs = {"package": "SOT-23"}
        merged = merge_specs(record, specs)
        assert merged["package"] == "SOT-323"

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
