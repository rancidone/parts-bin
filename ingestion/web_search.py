"""
Open-web datasheet search for confirmed search escalation.

Currently supports Brave Search API only. Returns a list of candidate PDF URLs
for the given query string, in result order.
"""

import httpx

import log

_logger = log.get_logger("parts_bin.web_search")

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


async def search_datasheet_pdfs(
    part_number: str,
    api_key: str,
    client: httpx.AsyncClient,
    max_results: int = 5,
) -> list[str]:
    """
    Search for datasheet PDFs for the given part number using Brave Search.

    Returns a list of PDF URLs (up to max_results), in result order.
    Returns an empty list on any failure.
    """
    query = f"{part_number} datasheet filetype:pdf"
    try:
        resp = await client.get(
            _BRAVE_SEARCH_URL,
            params={"q": query, "count": max_results},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("web", {}).get("results", [])
        pdf_urls = [
            r["url"]
            for r in results
            if r.get("url", "").lower().endswith(".pdf")
        ]
        _logger.debug("web search results", extra={
            "part_number": part_number,
            "total_results": len(results),
            "pdf_urls_found": len(pdf_urls),
        })
        return pdf_urls
    except Exception as exc:
        _logger.warning("web search failed", extra={
            "part_number": part_number,
            "error": str(exc),
        })
        return []
