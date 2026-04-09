"""
External spec lookup for discrete/IC parts.

Provider priority: LCSC (no auth) -> Digikey (OAuth2 client credentials).
Lookup failure is non-fatal; callers receive a structured enrichment result.
"""

from collections import defaultdict

import httpx

import log

_logger = log.get_logger("parts_bin.lookup")

# ---------------------------------------------------------------------------
# LCSC
# ---------------------------------------------------------------------------

async def _lcsc_lookup(part_number: str, client: httpx.AsyncClient) -> dict | None:
    """
    Search LCSC for a part number. Returns a dict with any of
    {manufacturer, description, package} that were found, or None on failure.
    """
    try:
        resp = await client.get(
            "https://lcsc.com/api/global/search/search",
            params={"q": part_number, "current_page": 1, "in_stock": False},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        products = data.get("result", {}).get("productSearchResultVO", {}).get("productList") or []
        # Find exact part number match (case-insensitive).
        for product in products:
            if (product.get("productModel") or "").upper() == part_number.upper():
                return _extract_lcsc_fields(product)
    except Exception:
        pass
    return None


def _extract_lcsc_fields(product: dict) -> dict:
    result = {}
    if product.get("productModel"):
        result["part_number"] = product["productModel"]
    if product.get("brandNameEn"):
        result["manufacturer"] = product["brandNameEn"]
    if product.get("productDescEn"):
        result["description"] = product["productDescEn"]
    if product.get("encapStandard"):
        result["package"] = product["encapStandard"]
    return result


def _lcsc_debug_summary(product: dict, part_number: str) -> dict:
    return {
        "requested_part_number": part_number,
        "manufacturer_part_number": product.get("productModel"),
        "product_url": product.get("productUrl"),
        "product_description": product.get("productDescEn"),
        "manufacturer": product.get("brandNameEn"),
        "package": product.get("encapStandard"),
    }


async def _lcsc_lookup_detailed(part_number: str, client: httpx.AsyncClient) -> dict:
    try:
        resp = await client.get(
            "https://lcsc.com/api/global/search/search",
            params={"q": part_number, "current_page": 1, "in_stock": False},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        products = data.get("result", {}).get("productSearchResultVO", {}).get("productList") or []
        for product in products:
            if (product.get("productModel") or "").upper() == part_number.upper():
                return {
                    "specs": _extract_lcsc_fields(product),
                    "debug": _lcsc_debug_summary(product, part_number),
                    "status": "ok",
                }
        return {"specs": None, "debug": None, "status": "no-match"}
    except httpx.ReadTimeout as exc:
        details = {"part_number": part_number, **_http_error_details(exc)}
        _logger.warning("lcsc lookup timeout", extra=details)
        return {"specs": None, "debug": None, "status": "timeout", "error": details}
    except Exception as exc:
        details = {"part_number": part_number, **_http_error_details(exc)}
        _logger.warning("lcsc lookup failed", extra=details)
        return {"specs": None, "debug": None, "status": "failed", "error": details}


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
        "product_description": product.get("ProductDescription"),
        "detailed_description": product.get("DetailedDescription"),
        "manufacturer": manufacturer.get("Name") if isinstance(manufacturer, dict) else manufacturer,
        "package": package_type.get("Name") if isinstance(package_type, dict) else package_type,
        "series": product.get("Series"),
    }


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
        "diagnostics": debug,
        "error": error,
    }


def _candidate_from_attempt(field_name: str, value: str, attempt: dict) -> dict:
    return {
        "field_name": field_name,
        "candidate_value": value,
        "source_tier": attempt["authority_tier"],
        "source_kind": attempt["source_kind"],
        "source_locator": attempt["source_locator"],
        "extraction_method": "api",
        "confidence_marker": "high",
        "conflict_status": "clear",
        "provider": attempt["provider"],
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
        })
    return records


async def fetch_specs_detailed(
    part_number: str,
    digikey_credentials: dict | None = None,
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
        tried_providers.append("lcsc")
        lcsc_result = await _lcsc_lookup_detailed(part_number, client)
        source_attempts.append(_build_source_attempt(
            provider="lcsc",
            authority_tier="primary_api",
            lookup_status=lcsc_result["status"],
            specs=lcsc_result.get("specs"),
            debug=lcsc_result.get("debug"),
            error=lcsc_result.get("error"),
        ))
        if lcsc_result.get("debug"):
            _logger.debug("lcsc raw response", extra=lcsc_result["debug"])

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

async def fetch_specs(
    part_number: str,
    digikey_credentials: dict | None = None,
) -> dict:
    """
    Fetch spec fields for a part number. Returns a (possibly empty) dict
    with any of {manufacturer, description, package} found.

    digikey_credentials: {"client_id": ..., "client_secret": ...} or None.
    """
    result = await fetch_specs_detailed(part_number, digikey_credentials)
    return result["chosen_updates"]


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
