"""
External spec lookup for discrete/IC parts.

Provider priority: LCSC (no auth) → Digikey (OAuth2 client credentials).
Lookup failure is non-fatal; caller receives whatever fields were found.
"""

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
    if product.get("brandNameEn"):
        result["manufacturer"] = product["brandNameEn"]
    if product.get("productDescEn"):
        result["description"] = product["productDescEn"]
    if product.get("encapStandard"):
        result["package"] = product["encapStandard"]
    return result


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

    async with httpx.AsyncClient() as client:
        tried_providers.append("lcsc")
        result = await _lcsc_lookup(part_number, client)
        if result:
            _logger.info("lookup match", extra={
                "provider": "lcsc",
                "part_number": part_number,
                "matched_part_number": part_number,
                "fields": sorted(result.keys()),
            })
            return {
                "specs": result,
                "provider": "lcsc",
                "matched_part_number": part_number,
                "tried_providers": tried_providers,
            }

        if digikey_credentials:
            tried_providers.append("digikey")
            digikey_result = await _digikey_lookup_detailed(
                part_number,
                digikey_credentials["client_id"],
                digikey_credentials["client_secret"],
                client,
            )
            result = digikey_result["specs"]
            if digikey_result["debug"]:
                _logger.debug("digikey raw response", extra=digikey_result["debug"])
            if result:
                _logger.info("lookup match", extra={
                    "provider": "digikey",
                    "part_number": part_number,
                    "matched_part_number": part_number,
                    "fields": sorted(result.keys()),
                })
                return {
                    "specs": result,
                    "provider": "digikey",
                    "matched_part_number": part_number,
                    "tried_providers": tried_providers,
                    "status": digikey_result["status"],
                }

    _logger.info("lookup no match", extra={
        "part_number": part_number,
        "tried_providers": tried_providers,
    })
    return {
        "specs": {},
        "provider": None,
        "matched_part_number": None,
        "tried_providers": tried_providers,
        "status": digikey_result["status"] if digikey_credentials else "no-match",
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
    return result["specs"]


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
