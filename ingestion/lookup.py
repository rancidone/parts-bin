"""
External spec lookup for discrete/IC parts.

Provider: Digikey (OAuth2 client credentials).
Lookup failure is non-fatal; callers receive a structured enrichment result.
"""

from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import httpx

import log
from ingestion.jlcparts_lookup import lookup_by_mpn as _jlcparts_lookup_by_mpn
from ingestion.pdf_extract import extract_pdf_candidates
from ingestion.source_extract import classify_content, extract_html_candidates

_logger = log.get_logger("parts_bin.lookup")
_FALLBACK_FIELD_SCOPE = ("manufacturer", "part_number", "package", "description")

# ---------------------------------------------------------------------------
# Digikey
# ---------------------------------------------------------------------------

async def _digikey_token(client_id: str, client_secret: str, client: httpx.AsyncClient) -> str | None:
    """Fetch a Digikey OAuth2 access token via client credentials."""
    try:
        resp = await client.post(
            "https://api.digikey.com/v1/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception:
        return None


async def _digikey_lookup(
    part_number: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
) -> dict | None:
    token = await _digikey_token(client_id, client_secret, client)
    if not token:
        return None
    try:
        resp = await client.get(
            f"https://api.digikey.com/products/v4/search/{part_number}/productdetails",
            headers={
                "Authorization": f"Bearer {token}",
                "X-DIGIKEY-Client-Id": client_id,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        product = data.get("Product", {})
        return _extract_digikey_fields(product)
    except Exception:
        return None


def _http_error_details(exc: Exception) -> dict:
    details = {
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    response = getattr(exc, "response", None)
    if response is not None:
        body = None
        try:
            body = response.text
        except Exception:
            body = None
        details["status_code"] = response.status_code
        details["response_body"] = body[:1000] if body else None
    return details


async def _digikey_lookup_detailed(
    part_number: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
) -> dict:
    token = await _digikey_token(client_id, client_secret, client)
    if not token:
        return {"specs": None, "debug": None, "status": "auth-failed"}

    last_error: dict | None = None
    for attempt in range(2):
        try:
            resp = await client.get(
                f"https://api.digikey.com/products/v4/search/{part_number}/productdetails",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-DIGIKEY-Client-Id": client_id,
                },
                timeout=20.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "specs": _extract_digikey_fields(data.get("Product", {})),
                "debug": _digikey_debug_summary(data, part_number),
                "status": "ok",
            }
        except httpx.ReadTimeout as exc:
            last_error = {
                "part_number": part_number,
                "attempt": attempt + 1,
                **_http_error_details(exc),
            }
            _logger.warning("digikey lookup timeout", extra=last_error)
        except Exception as exc:
            last_error = {
                "part_number": part_number,
                "attempt": attempt + 1,
                **_http_error_details(exc),
            }
            _logger.warning("digikey lookup failed", extra=last_error)
            return {"specs": None, "debug": None, "status": "failed", "error": last_error}

    return {"specs": None, "debug": None, "status": "timeout", "error": last_error}


def _extract_digikey_fields(product: dict) -> dict:
    result = {}
    if product.get("ManufacturerPartNumber"):
        result["part_number"] = product["ManufacturerPartNumber"]
    mfr = product.get("Manufacturer", {})
    if isinstance(mfr, dict) and mfr.get("Name"):
        result["manufacturer"] = mfr["Name"]
    elif isinstance(mfr, str) and mfr:
        result["manufacturer"] = mfr
    if product.get("ProductDescription"):
        result["description"] = product["ProductDescription"]
    if product.get("PackageType", {}).get("Name"):
        result["package"] = product["PackageType"]["Name"]
    return result


def _digikey_debug_summary(data: dict, part_number: str) -> dict:
    product = data.get("Product", {}) or {}
    manufacturer = product.get("Manufacturer", {})
    package_type = product.get("PackageType", {})

    return {
        "requested_part_number": part_number,
        "digikey_part_number": product.get("DigiKeyPartNumber"),
        "manufacturer_part_number": product.get("ManufacturerPartNumber"),
        "product_url": product.get("ProductUrl"),
        "datasheet_url": _first_present_url(product, "DatasheetUrl", "PrimaryDatasheet", "PrimaryDatasheetUrl"),
        "product_description": product.get("ProductDescription"),
        "detailed_description": product.get("DetailedDescription"),
        "manufacturer": manufacturer.get("Name") if isinstance(manufacturer, dict) else manufacturer,
        "package": package_type.get("Name") if isinstance(package_type, dict) else package_type,
        "series": product.get("Series"),
    }


def _first_present_url(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            for nested_key in ("Url", "url", "Value", "value"):
                nested_value = value.get(nested_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
        elif isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_source_attempt(
    provider: str,
    authority_tier: str,
    lookup_status: str,
    specs: dict | None,
    debug: dict | None,
    error: dict | None = None,
) -> dict:
    locator = None
    if debug:
        locator = debug.get("product_url") or debug.get("manufacturer_part_number") or debug.get("requested_part_number")

    return {
        "provider": provider,
        "authority_tier": authority_tier,
        "source_kind": "api",
        "status": lookup_status,
        "source_locator": locator,
        "fields": specs or {},
        "field_metadata": {},
        "diagnostics": debug,
        "warnings": [],
        "error": error,
    }


def _candidate_from_attempt(field_name: str, value: str, attempt: dict) -> dict:
    field_metadata = (attempt.get("field_metadata") or {}).get(field_name, {})
    return {
        "field_name": field_name,
        "candidate_value": value,
        "source_tier": attempt["authority_tier"],
        "source_kind": attempt["source_kind"],
        "source_locator": attempt["source_locator"],
        "extraction_method": field_metadata.get("method", "api"),
        "confidence_marker": "high",
        "conflict_status": "clear",
        "provider": attempt["provider"],
        "evidence": field_metadata.get("evidence"),
    }


def _build_field_candidates(source_attempts: list[dict]) -> dict[str, list[dict]]:
    candidates: dict[str, list[dict]] = defaultdict(list)
    for attempt in source_attempts:
        if attempt["status"] != "ok":
            continue
        for field_name, value in attempt["fields"].items():
            if value is None:
                continue
            candidates[field_name].append(_candidate_from_attempt(field_name, value, attempt))
    return dict(candidates)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _reconcile_candidates(
    part_number: str,
    field_candidates: dict[str, list[dict]],
    source_attempts: list[dict],
) -> tuple[dict, list[dict], list[dict], str]:
    identifying_fields = ("part_number", "manufacturer", "package", "part_category", "profile")
    conflicts: list[dict] = []
    chosen_updates: dict[str, str] = {}
    chosen_candidates: list[dict] = []

    for field_name in identifying_fields:
        candidates = field_candidates.get(field_name, [])
        unique_values = _dedupe_preserve_order([candidate["candidate_value"] for candidate in candidates])
        if len(unique_values) > 1:
            conflicts.append({
                "field_name": field_name,
                "values": unique_values,
                "providers": [candidate["provider"] for candidate in candidates],
            })
            for candidate in candidates:
                candidate["conflict_status"] = "conflict"

    if conflicts:
        return {}, [], conflicts, "conflict"

    for field_name, candidates in field_candidates.items():
        if not candidates:
            continue
        chosen = candidates[0]
        if field_name == "description" and len(candidates) > 1:
            longest = max(candidates, key=lambda candidate: len(candidate["candidate_value"]))
            chosen = longest
            chosen["extraction_method"] = "api-direct"
        chosen_updates[field_name] = chosen["candidate_value"]
        chosen_candidates.append(chosen)

    if chosen_updates:
        return chosen_updates, chosen_candidates, conflicts, "saved"

    statuses = [attempt["status"] for attempt in source_attempts]
    if any(status == "timeout" for status in statuses):
        return {}, [], conflicts, "timeout"
    if any(status == "failed" for status in statuses):
        return {}, [], conflicts, "failed"
    if any(status == "ok" for status in statuses):
        return {}, [], conflicts, "incomplete"
    return {}, [], conflicts, "no_match"


def _provenance_from_candidates(chosen_candidates: list[dict]) -> list[dict]:
    records: list[dict] = []
    for candidate in chosen_candidates:
        records.append({
            "field_name": candidate["field_name"],
            "field_value": candidate["candidate_value"],
            "source_tier": candidate["source_tier"],
            "source_kind": candidate["source_kind"],
            "source_locator": candidate["source_locator"],
            "extraction_method": candidate["extraction_method"],
            "confidence_marker": candidate["confidence_marker"],
            "conflict_status": candidate["conflict_status"],
            "normalization_method": "direct_copy",
            "competing_candidates": [],
            "evidence": candidate.get("evidence"),
        })
    return records


def _missing_fallback_fields(chosen_updates: dict) -> list[str]:
    return [field_name for field_name in _FALLBACK_FIELD_SCOPE if not chosen_updates.get(field_name)]


async def _fetch_api_derived_product_page(
    source_attempts: list[dict],
    chosen_updates: dict,
    client: httpx.AsyncClient,
) -> dict | None:
    missing_fields = _missing_fallback_fields(chosen_updates)
    if not missing_fields:
        return None

    for attempt in source_attempts:
        if attempt["status"] != "ok" or not attempt["diagnostics"]:
            continue
        product_url = attempt["diagnostics"].get("product_url")
        if not product_url:
            continue
        try:
            resp = await client.get(product_url, timeout=10.0, follow_redirects=True)
            resp.raise_for_status()
            classification = classify_content(resp.headers.get("content-type"), resp.text)
            if classification != "structured_html_product_page":
                return {
                    "provider": attempt["provider"],
                    "authority_tier": "api_derived_page",
                    "source_kind": "product_page",
                    "status": "unsupported-content",
                    "source_locator": str(resp.url),
                    "fields": {},
                    "diagnostics": {
                        "requested_url": product_url,
                        "resolved_url": str(resp.url),
                        "content_type": resp.headers.get("content-type"),
                        "classification": classification,
                    },
                    "error": None,
                }

            extracted_candidates = extract_html_candidates(str(resp.url), resp.text)
            filtered_candidates = {
                field_name: candidate
                for field_name, candidate in extracted_candidates.items()
                if field_name in missing_fields
            }
            return {
                "provider": attempt["provider"],
                "authority_tier": "api_derived_page",
                "source_kind": "product_page",
                "status": "ok" if filtered_candidates else "no-candidates",
                "source_locator": str(resp.url),
                "fields": {
                    field_name: candidate["value"]
                    for field_name, candidate in filtered_candidates.items()
                },
                "field_metadata": filtered_candidates,
                "diagnostics": {
                    "requested_url": product_url,
                    "resolved_url": str(resp.url),
                    "content_type": resp.headers.get("content-type"),
                    "classification": classification,
                    "provider_host": urlparse(str(resp.url)).netloc.lower(),
                },
                "warnings": [] if filtered_candidates else ["extractor-produced-no-candidates"],
                "error": None,
            }
        except httpx.ReadTimeout as exc:
            return {
                "provider": attempt["provider"],
                "authority_tier": "api_derived_page",
                "source_kind": "product_page",
                "status": "timeout",
                "source_locator": product_url,
                "fields": {},
                "field_metadata": {},
                "diagnostics": {"requested_url": product_url},
                "warnings": ["retrieval-timeout"],
                "error": _http_error_details(exc),
            }
        except Exception as exc:
            return {
                "provider": attempt["provider"],
                "authority_tier": "api_derived_page",
                "source_kind": "product_page",
                "status": "failed",
                "source_locator": product_url,
                "fields": {},
                "field_metadata": {},
                "diagnostics": {"requested_url": product_url},
                "warnings": ["retrieval-failed"],
                "error": _http_error_details(exc),
            }
    return None


async def _fetch_api_derived_pdf(
    source_attempts: list[dict],
    chosen_updates: dict,
    client: httpx.AsyncClient,
) -> dict | None:
    missing_fields = _missing_fallback_fields(chosen_updates)
    if not missing_fields:
        return None

    for attempt in source_attempts:
        if attempt["status"] != "ok" or not attempt["diagnostics"]:
            continue
        datasheet_url = attempt["diagnostics"].get("datasheet_url")
        if not datasheet_url:
            continue
        try:
            resp = await client.get(datasheet_url, timeout=10.0, follow_redirects=True)
            resp.raise_for_status()
            classification = classify_content(resp.headers.get("content-type"), resp.content.decode("latin-1", errors="ignore"))
            if classification != "pdf_document":
                return {
                    "provider": attempt["provider"],
                    "authority_tier": "api_derived_pdf",
                    "source_kind": "pdf_document",
                    "status": "unsupported-content",
                    "source_locator": str(resp.url),
                    "fields": {},
                    "field_metadata": {},
                    "diagnostics": {
                        "requested_url": datasheet_url,
                        "resolved_url": str(resp.url),
                        "content_type": resp.headers.get("content-type"),
                        "classification": classification,
                    },
                    "warnings": ["pdf-url-did-not-return-pdf"],
                    "error": None,
                }

            extracted_candidates = extract_pdf_candidates(resp.content)
            filtered_candidates = {
                field_name: candidate
                for field_name, candidate in extracted_candidates.items()
                if field_name in missing_fields
            }
            return {
                "provider": attempt["provider"],
                "authority_tier": "api_derived_pdf",
                "source_kind": "pdf_document",
                "status": "ok" if filtered_candidates else "no-candidates",
                "source_locator": str(resp.url),
                "fields": {
                    field_name: candidate["value"]
                    for field_name, candidate in filtered_candidates.items()
                },
                "field_metadata": filtered_candidates,
                "diagnostics": {
                    "requested_url": datasheet_url,
                    "resolved_url": str(resp.url),
                    "content_type": resp.headers.get("content-type"),
                    "classification": classification,
                },
                "warnings": [] if filtered_candidates else ["pdf-extractor-produced-no-candidates"],
                "error": None,
            }
        except httpx.ReadTimeout as exc:
            return {
                "provider": attempt["provider"],
                "authority_tier": "api_derived_pdf",
                "source_kind": "pdf_document",
                "status": "timeout",
                "source_locator": datasheet_url,
                "fields": {},
                "field_metadata": {},
                "diagnostics": {"requested_url": datasheet_url},
                "warnings": ["pdf-retrieval-timeout"],
                "error": _http_error_details(exc),
            }
        except Exception as exc:
            return {
                "provider": attempt["provider"],
                "authority_tier": "api_derived_pdf",
                "source_kind": "pdf_document",
                "status": "failed",
                "source_locator": datasheet_url,
                "fields": {},
                "field_metadata": {},
                "diagnostics": {"requested_url": datasheet_url},
                "warnings": ["pdf-retrieval-failed"],
                "error": _http_error_details(exc),
            }
    return None


async def fetch_specs_detailed(
    part_number: str,
    digikey_credentials: dict | None = None,
    jlcparts_db_path: str | None = None,
) -> dict:
    """
    Fetch spec fields for a part number with provider-level outcome details.

    Returns:
      {
        "specs": dict,
        "provider": str | None,
        "matched_part_number": str | None,
        "tried_providers": list[str],
      }
    """
    tried_providers: list[str] = []
    source_attempts: list[dict] = []

    async with httpx.AsyncClient() as client:
        if jlcparts_db_path and Path(jlcparts_db_path).exists():
            tried_providers.append("jlcparts")
            jlcparts_result = _jlcparts_lookup_by_mpn(jlcparts_db_path, part_number)
            source_attempts.append(_build_source_attempt(
                provider="jlcparts",
                authority_tier="local_db",
                lookup_status=jlcparts_result["status"],
                specs=jlcparts_result.get("specs"),
                debug=jlcparts_result.get("debug"),
                error=jlcparts_result.get("error"),
            ))
            if jlcparts_result.get("debug"):
                _logger.debug("jlcparts raw result", extra=jlcparts_result["debug"])

        if digikey_credentials:
            tried_providers.append("digikey")
            digikey_result = await _digikey_lookup_detailed(
                part_number,
                digikey_credentials["client_id"],
                digikey_credentials["client_secret"],
                client,
            )
            source_attempts.append(_build_source_attempt(
                provider="digikey",
                authority_tier="primary_api",
                lookup_status=digikey_result["status"],
                specs=digikey_result.get("specs"),
                debug=digikey_result.get("debug"),
                error=digikey_result.get("error"),
            ))
            if digikey_result.get("debug"):
                _logger.debug("digikey raw response", extra=digikey_result["debug"])

        field_candidates = _build_field_candidates(source_attempts)
        chosen_updates, chosen_candidates, conflicts, outcome = _reconcile_candidates(
            part_number,
            field_candidates,
            source_attempts,
        )

        if outcome != "conflict":
            page_attempt = await _fetch_api_derived_product_page(source_attempts, chosen_updates, client)
            if page_attempt is not None:
                source_attempts.append(page_attempt)
                field_candidates = _build_field_candidates(source_attempts)
                chosen_updates, chosen_candidates, conflicts, outcome = _reconcile_candidates(
                    part_number,
                    field_candidates,
                    source_attempts,
                )
            pdf_attempt = await _fetch_api_derived_pdf(source_attempts, chosen_updates, client)
            if pdf_attempt is not None:
                source_attempts.append(pdf_attempt)
                field_candidates = _build_field_candidates(source_attempts)
                chosen_updates, chosen_candidates, conflicts, outcome = _reconcile_candidates(
                    part_number,
                    field_candidates,
                    source_attempts,
                )

    provenance = _provenance_from_candidates(chosen_candidates)
    provider = chosen_candidates[0]["provider"] if chosen_candidates else None

    if outcome == "saved":
        _logger.info("lookup match", extra={
            "part_number": part_number,
            "provider": provider,
            "fields": sorted(chosen_updates.keys()),
            "outcome": outcome,
        })
    else:
        _logger.info("lookup outcome", extra={
            "part_number": part_number,
            "tried_providers": tried_providers,
            "outcome": outcome,
            "conflicts": conflicts,
        })

    return {
        "request": {"part_number": part_number, "inventory_id": None},
        "source_attempts": source_attempts,
        "field_candidates": field_candidates,
        "chosen_updates": chosen_updates,
        "outcome": outcome,
        "requires_confirmation": outcome == "conflict",
        "status_message": None,
        "durable_provenance": provenance,
        "conflicts": conflicts,
        "specs": chosen_updates,
        "provider": provider,
        "matched_part_number": chosen_updates.get("part_number"),
        "tried_providers": tried_providers,
        "status": outcome,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def merge_specs(record: dict, specs: dict) -> dict:
    """
    Merge fetched specs into the part record.

    Rules:
    - manufacturer and description: always written from specs (these come from lookup).
    - package: only written if the record has package=None (don't overwrite user-provided).
    """
    merged = dict(record)
    if specs.get("manufacturer"):
        merged["manufacturer"] = specs["manufacturer"]
    if specs.get("description"):
        merged["description"] = specs["description"]
    if merged.get("package") is None and specs.get("package"):
        merged["package"] = specs["package"]
    return merged
