"""
External spec lookup for discrete/IC parts.

Provider: Digikey (OAuth2 client credentials).
Lookup failure is non-fatal; callers receive a structured enrichment result.
"""

from collections import defaultdict
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qs, urlparse

import httpx

import log
from ingestion.jlcparts_lookup import lookup_by_mpn as _jlcparts_lookup_by_mpn
from ingestion.pdf_extract import extract_pdf_candidates
from ingestion.source_extract import classify_content, extract_html_candidates

_logger = log.get_logger("parts_bin.lookup")
_FALLBACK_FIELD_SCOPE = ("manufacturer", "part_number", "package", "description")
_WITHHELD_FALLBACK_FIELDS = ("part_category", "profile", "value")


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 1)

# ---------------------------------------------------------------------------
# Digikey
# ---------------------------------------------------------------------------

async def _digikey_token(client_id: str, client_secret: str, client: httpx.AsyncClient) -> str | None:
    """Fetch a Digikey OAuth2 access token via client credentials."""
    started = perf_counter()
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
        _logger.info("digikey token fetched", extra={
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "status_code": resp.status_code,
        })
        return resp.json().get("access_token")
    except Exception as exc:
        _logger.warning("digikey token failed", extra={
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            **_http_error_details(exc),
        })
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
    lookup_started = perf_counter()
    token = await _digikey_token(client_id, client_secret, client)
    if not token:
        return {"specs": None, "debug": None, "status": "auth-failed"}

    last_error: dict | None = None
    for attempt in range(2):
        attempt_started = perf_counter()
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
            _logger.info("digikey productdetails fetched", extra={
                "part_number": part_number,
                "attempt": attempt + 1,
                "latency_ms": round((perf_counter() - attempt_started) * 1000, 1),
                "total_latency_ms": round((perf_counter() - lookup_started) * 1000, 1),
                "status_code": resp.status_code,
            })
            return {
                "specs": _extract_digikey_fields(data.get("Product", {})),
                "debug": _digikey_debug_summary(data, part_number),
                "status": "ok",
            }
        except httpx.ReadTimeout as exc:
            last_error = {
                "part_number": part_number,
                "attempt": attempt + 1,
                "latency_ms": round((perf_counter() - attempt_started) * 1000, 1),
                "total_latency_ms": round((perf_counter() - lookup_started) * 1000, 1),
                **_http_error_details(exc),
            }
            _logger.warning("digikey lookup timeout", extra=last_error)
        except Exception as exc:
            last_error = {
                "part_number": part_number,
                "attempt": attempt + 1,
                "latency_ms": round((perf_counter() - attempt_started) * 1000, 1),
                "total_latency_ms": round((perf_counter() - lookup_started) * 1000, 1),
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


def _competing_candidates(chosen: dict, candidates: list[dict]) -> list[dict]:
    competing: list[dict] = []
    seen: set[tuple[str, str, str | None]] = set()
    chosen_key = (
        chosen["candidate_value"].strip().casefold(),
        chosen["source_tier"],
        chosen.get("source_locator"),
    )
    for candidate in candidates:
        key = (
            candidate["candidate_value"].strip().casefold(),
            candidate["source_tier"],
            candidate.get("source_locator"),
        )
        if key == chosen_key or key in seen:
            continue
        seen.add(key)
        competing.append({
            "field_value": candidate["candidate_value"],
            "source_tier": candidate["source_tier"],
            "source_kind": candidate["source_kind"],
            "source_locator": candidate.get("source_locator"),
            "extraction_method": candidate["extraction_method"],
            "provider": candidate.get("provider"),
            "evidence": candidate.get("evidence"),
            "conflict_status": candidate.get("conflict_status", "clear"),
        })
    return competing


def _choose_description_candidate(candidates: list[dict]) -> dict:
    chosen = max(
        candidates,
        key=lambda candidate: (
            len(candidate["candidate_value"]),
            -candidates.index(candidate),
        ),
    ).copy()
    competing = _competing_candidates(chosen, candidates)
    if competing:
        chosen["extraction_method"] = "source-description-merge"
        chosen["normalization_method"] = "source_description_merge"
    else:
        chosen["normalization_method"] = "direct_copy"
    chosen["competing_candidates"] = competing
    return chosen


def _choose_field_candidate(field_name: str, candidates: list[dict]) -> dict:
    if field_name == "description":
        return _choose_description_candidate(candidates)

    chosen = candidates[0].copy()
    chosen["normalization_method"] = "direct_copy"
    chosen["competing_candidates"] = _competing_candidates(chosen, candidates)
    return chosen


def _build_field_candidates(source_attempts: list[dict]) -> dict[str, list[dict]]:
    candidates: dict[str, list[dict]] = defaultdict(list)
    for attempt in source_attempts:
        if attempt["status"] != "ok":
            continue
        for field_name, value in attempt["fields"].items():
            if value is None or field_name not in _FALLBACK_FIELD_SCOPE:
                continue
            candidates[field_name].append(_candidate_from_attempt(field_name, value, attempt))
    return dict(candidates)


def _collect_withheld_candidates(source_attempts: list[dict]) -> dict[str, list[dict]]:
    withheld: dict[str, list[dict]] = defaultdict(list)
    for attempt in source_attempts:
        if attempt["status"] != "ok":
            continue
        for field_name, value in attempt["fields"].items():
            if field_name not in _WITHHELD_FALLBACK_FIELDS or value is None:
                continue
            withheld[field_name].append(_candidate_from_attempt(field_name, value, attempt))
    return dict(withheld)


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
        if len(candidates) <= 1:
            continue
        # Group by authority tier. Cross-tier disagreements are resolved by
        # priority order (first source_attempt wins). Only flag conflicts when
        # candidates from the *same* tier disagree with each other.
        by_tier: dict[str, list[dict]] = {}
        for candidate in candidates:
            by_tier.setdefault(candidate["source_tier"], []).append(candidate)
        for tier, tier_candidates in by_tier.items():
            unique_values = _dedupe_preserve_order(
                [c["candidate_value"] for c in tier_candidates]
            )
            if len(unique_values) > 1:
                conflicts.append({
                    "field_name": field_name,
                    "values": unique_values,
                    "providers": [c["provider"] for c in tier_candidates],
                })
                for candidate in tier_candidates:
                    candidate["conflict_status"] = "conflict"

    if conflicts:
        return {}, [], conflicts, "conflict"

    for field_name, candidates in field_candidates.items():
        if not candidates:
            continue
        chosen = _choose_field_candidate(field_name, candidates)
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
            "normalization_method": candidate.get("normalization_method", "direct_copy"),
            "competing_candidates": candidate.get("competing_candidates", []),
            "evidence": candidate.get("evidence"),
        })
    return records


def _missing_fallback_fields(chosen_updates: dict) -> list[str]:
    return [field_name for field_name in _FALLBACK_FIELD_SCOPE if not chosen_updates.get(field_name)]


def _filter_fallback_candidates(candidates: dict[str, dict]) -> dict[str, dict]:
    return {
        field_name: candidate
        for field_name, candidate in candidates.items()
        if field_name in _FALLBACK_FIELD_SCOPE
    }


async def _fetch_api_derived_product_page(
    source_attempts: list[dict],
    chosen_updates: dict,
    client: httpx.AsyncClient,
) -> dict | None:
    missing_fields = _missing_fallback_fields(chosen_updates)
    if not missing_fields:
        return None

    seen_urls: set[str] = set()
    for attempt in source_attempts:
        if attempt["status"] != "ok" or not attempt["diagnostics"]:
            continue
        product_url = attempt["diagnostics"].get("product_url")
        if not product_url or product_url in seen_urls:
            continue
        seen_urls.add(product_url)
        try:
            resp = await client.get(product_url, timeout=10.0, follow_redirects=True)
            resp.raise_for_status()
            classification = classify_content(resp.headers.get("content-type"), resp.text)
            if classification != "structured_html_product_page":
                _logger.debug("product page unsupported content", extra={
                    "provider": attempt["provider"],
                    "url": product_url,
                    "classification": classification,
                })
                continue

            extracted_candidates = extract_html_candidates(str(resp.url), resp.text)
            filtered_candidates = _filter_fallback_candidates(extracted_candidates)
            if filtered_candidates:
                return {
                    "provider": attempt["provider"],
                    "authority_tier": "api_derived_page",
                    "source_kind": "product_page",
                    "status": "ok",
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
                    "warnings": [],
                    "error": None,
                }
            _logger.debug("product page no candidates", extra={
                "provider": attempt["provider"],
                "url": product_url,
            })
        except httpx.ReadTimeout:
            _logger.debug("product page timeout", extra={"provider": attempt["provider"], "url": product_url})
        except Exception as exc:
            _logger.debug("product page failed", extra={"provider": attempt["provider"], "url": product_url, **_http_error_details(exc)})
    return None


def _resolve_datasheet_url(url: str) -> list[str]:
    """
    Return the canonical URL(s) to try for a datasheet.

    Some distributors (e.g. DigiKey) link to manufacturer redirect/tracking
    pages instead of the PDF directly.  When we detect a known redirect pattern
    we extract the inner URL and prepend it so it is tried first.
    """
    candidates = [url]
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    # TI suppproductinfo redirect: ?gotoUrl=<actual_url>
    goto = qs.get("gotoUrl") or qs.get("gotourl")
    if goto:
        candidates.insert(0, goto[0])
    return candidates


async def _fetch_api_derived_pdf(
    source_attempts: list[dict],
    chosen_updates: dict,
    client: httpx.AsyncClient,
) -> dict | None:
    missing_fields = _missing_fallback_fields(chosen_updates)
    if not missing_fields:
        return None

    seen_urls: set[str] = set()
    for attempt in source_attempts:
        if attempt["status"] != "ok" or not attempt["diagnostics"]:
            continue
        datasheet_url = attempt["diagnostics"].get("datasheet_url")
        if not datasheet_url:
            continue
        for url_to_try in _resolve_datasheet_url(datasheet_url):
            if url_to_try in seen_urls:
                continue
            seen_urls.add(url_to_try)
            try:
                resp = await client.get(url_to_try, timeout=10.0, follow_redirects=True)
                resp.raise_for_status()
                classification = classify_content(resp.headers.get("content-type"), resp.content.decode("latin-1", errors="ignore"))
                if classification != "pdf_document":
                    _logger.debug("datasheet url not a pdf", extra={
                        "provider": attempt["provider"],
                        "url": url_to_try,
                        "classification": classification,
                    })
                    continue
                extracted_candidates = extract_pdf_candidates(resp.content)
                ambiguous_fields = sorted(
                    field_name
                    for field_name, candidate in extracted_candidates.items()
                    if candidate.get("ambiguous")
                )
                all_filtered_candidates = _filter_fallback_candidates(extracted_candidates)
                warnings: list[str] = []
                if ambiguous_fields:
                    warnings.append(
                        "ambiguous_pdf_candidates:" + ",".join(ambiguous_fields)
                    )
                if all_filtered_candidates:
                    return {
                        "provider": attempt["provider"],
                        "authority_tier": "api_derived_pdf",
                        "source_kind": "pdf_document",
                        "status": "ok",
                        "source_locator": str(resp.url),
                        "fields": {
                            field_name: candidate["value"]
                            for field_name, candidate in all_filtered_candidates.items()
                            if not candidate.get("ambiguous")
                        },
                        "field_metadata": all_filtered_candidates,
                        "diagnostics": {
                            "requested_url": url_to_try,
                            "resolved_url": str(resp.url),
                            "content_type": resp.headers.get("content-type"),
                            "classification": classification,
                        },
                        "warnings": warnings,
                        "error": None,
                    }
                if warnings:
                    return {
                        "provider": attempt["provider"],
                        "authority_tier": "api_derived_pdf",
                        "source_kind": "pdf_document",
                        "status": "ok",
                        "source_locator": str(resp.url),
                        "fields": {},
                        "field_metadata": {},
                        "diagnostics": {
                            "requested_url": url_to_try,
                            "resolved_url": str(resp.url),
                            "content_type": resp.headers.get("content-type"),
                            "classification": classification,
                        },
                        "warnings": warnings,
                        "error": None,
                    }
                _logger.debug("pdf no candidates", extra={
                    "provider": attempt["provider"],
                    "url": url_to_try,
                })
            except httpx.ReadTimeout:
                _logger.debug("pdf retrieval timeout", extra={"provider": attempt["provider"], "url": url_to_try})
            except Exception:
                _logger.debug("pdf retrieval failed", extra={"provider": attempt["provider"], "url": url_to_try})
    return None


async def fetch_specs_detailed(
    part_number: str,
    digikey_credentials: dict | None = None,
    jlcparts_db_path: str | None = None,
    llm=None,
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
    total_started = perf_counter()
    stage_timings_ms: dict[str, float] = {}

    async with httpx.AsyncClient() as client:
        if jlcparts_db_path and Path(jlcparts_db_path).exists():
            jlcparts_started = perf_counter()
            tried_providers.append("jlcparts")
            jlcparts_result = _jlcparts_lookup_by_mpn(jlcparts_db_path, part_number)
            stage_timings_ms["jlcparts_lookup"] = _elapsed_ms(jlcparts_started)
            _logger.info("jlcparts lookup finished", extra={
                "part_number": part_number,
                "latency_ms": stage_timings_ms["jlcparts_lookup"],
                "status": jlcparts_result["status"],
            })
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
            digikey_started = perf_counter()
            digikey_result = await _digikey_lookup_detailed(
                part_number,
                digikey_credentials["client_id"],
                digikey_credentials["client_secret"],
                client,
            )
            stage_timings_ms["digikey_lookup"] = _elapsed_ms(digikey_started)
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

        reconcile_started = perf_counter()
        field_candidates = _build_field_candidates(source_attempts)
        withheld_candidates = _collect_withheld_candidates(source_attempts)
        chosen_updates, chosen_candidates, conflicts, outcome = _reconcile_candidates(
            part_number,
            field_candidates,
            source_attempts,
        )
        stage_timings_ms["initial_reconcile"] = _elapsed_ms(reconcile_started)

        if outcome != "conflict":
            page_started = perf_counter()
            page_attempt = await _fetch_api_derived_product_page(source_attempts, chosen_updates, client)
            stage_timings_ms["api_derived_page_fetch"] = _elapsed_ms(page_started)
            if page_attempt is not None:
                source_attempts.append(page_attempt)
                page_reconcile_started = perf_counter()
                field_candidates = _build_field_candidates(source_attempts)
                withheld_candidates = _collect_withheld_candidates(source_attempts)
                chosen_updates, chosen_candidates, conflicts, outcome = _reconcile_candidates(
                    part_number,
                    field_candidates,
                    source_attempts,
                )
                stage_timings_ms["api_derived_page_reconcile"] = _elapsed_ms(page_reconcile_started)
            else:
                stage_timings_ms["api_derived_page_reconcile"] = 0.0
            pdf_started = perf_counter()
            pdf_attempt = await _fetch_api_derived_pdf(source_attempts, chosen_updates, client)
            stage_timings_ms["api_derived_pdf_fetch"] = _elapsed_ms(pdf_started)
            if pdf_attempt is not None:
                source_attempts.append(pdf_attempt)
                pdf_reconcile_started = perf_counter()
                field_candidates = _build_field_candidates(source_attempts)
                withheld_candidates = _collect_withheld_candidates(source_attempts)
                chosen_updates, chosen_candidates, conflicts, outcome = _reconcile_candidates(
                    part_number,
                    field_candidates,
                    source_attempts,
                )
                stage_timings_ms["api_derived_pdf_reconcile"] = _elapsed_ms(pdf_reconcile_started)
            else:
                stage_timings_ms["api_derived_pdf_reconcile"] = 0.0
        else:
            stage_timings_ms["api_derived_page_fetch"] = 0.0
            stage_timings_ms["api_derived_page_reconcile"] = 0.0
            stage_timings_ms["api_derived_pdf_fetch"] = 0.0
            stage_timings_ms["api_derived_pdf_reconcile"] = 0.0

    if llm is not None and outcome != "conflict":
        desc_candidates = field_candidates.get("description", [])
        if len(desc_candidates) >= 2:
            descriptions = _dedupe_preserve_order(
                [c["candidate_value"] for c in desc_candidates]
            )
            if len(descriptions) >= 2:
                merge_started = perf_counter()
                try:
                    merged = await llm.merge_descriptions(descriptions)
                    stage_timings_ms["description_merge"] = _elapsed_ms(merge_started)
                    if merged:
                        chosen_updates["description"] = merged
                        # Replace the chosen description candidate with a merge record.
                        merged_candidate = desc_candidates[0].copy()
                        merged_candidate["candidate_value"] = merged
                        merged_candidate["extraction_method"] = "source-description-merge"
                        merged_candidate["normalization_method"] = "llm_description_merge"
                        merged_candidate["competing_candidates"] = [
                            {
                                "field_name": "description",
                                "field_value": c["candidate_value"],
                                "source_tier": c["source_tier"],
                                "source_kind": c["source_kind"],
                                "source_locator": c.get("source_locator"),
                                "extraction_method": c["extraction_method"],
                                "provider": c.get("provider"),
                                "evidence": c.get("evidence"),
                                "conflict_status": "merged",
                            }
                            for c in desc_candidates
                        ]
                        # Update chosen_candidates list.
                        chosen_candidates = [
                            merged_candidate if c.get("field_name") == "description" else c
                            for c in chosen_candidates
                        ]
                        _logger.info("description merge ok", extra={
                            "part_number": part_number,
                            "source_count": len(descriptions),
                        })
                except Exception:
                    stage_timings_ms["description_merge"] = _elapsed_ms(merge_started)
                    _logger.warning("description merge failed, keeping longest", extra={
                        "part_number": part_number,
                    })

    for field_name, candidates in withheld_candidates.items():
        if not candidates:
            continue
        values = _dedupe_preserve_order([candidate["candidate_value"] for candidate in candidates])
        for attempt in source_attempts:
            if attempt["status"] != "ok":
                continue
            if field_name not in attempt.get("fields", {}):
                continue
            attempt.setdefault("warnings", []).append(
                f"withheld_non_deterministic_field:{field_name}"
            )
        _logger.info("lookup withheld candidates", extra={
            "part_number": part_number,
            "field_name": field_name,
            "candidate_values": values,
        })

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

    total_latency_ms = _elapsed_ms(total_started)
    stage_timings_ms["total"] = total_latency_ms
    _logger.info("lookup timing breakdown", extra={
        "part_number": part_number,
        "outcome": outcome,
        "stage_timings_ms": stage_timings_ms,
        "source_attempt_statuses": [
            {
                "provider": attempt["provider"],
                "authority_tier": attempt["authority_tier"],
                "status": attempt["status"],
                "source_kind": attempt["source_kind"],
            }
            for attempt in source_attempts
        ],
    })
    _logger.info("lookup finished", extra={
        "part_number": part_number,
        "latency_ms": total_latency_ms,
        "tried_providers": tried_providers,
        "outcome": outcome,
    })

    return {
        "request": {"part_number": part_number, "inventory_id": None},
        "source_attempts": source_attempts,
        "field_candidates": field_candidates,
        "withheld_candidates": withheld_candidates,
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
        "stage_timings_ms": stage_timings_ms,
        "status": outcome,
    }
