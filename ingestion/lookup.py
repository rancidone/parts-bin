"""
External spec lookup for discrete/IC parts.

Provider priority: LCSC (no auth) → Digikey (OAuth2 client credentials).
Lookup failure is non-fatal; caller receives whatever fields were found.
"""

import httpx

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
        # Fallback: first result if any.
        if products:
            return _extract_lcsc_fields(products[0])
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
    async with httpx.AsyncClient() as client:
        result = await _lcsc_lookup(part_number, client)
        if result:
            return result

        if digikey_credentials:
            result = await _digikey_lookup(
                part_number,
                digikey_credentials["client_id"],
                digikey_credentials["client_secret"],
                client,
            )
            if result:
                return result

    return {}


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
