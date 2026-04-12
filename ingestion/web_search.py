"""
Open-web datasheet search for confirmed search escalation.

Uses DuckDuckGo HTML search — no API key required.
Parses the redirect hrefs DDG embeds in result links to extract candidate URLs.
"""

import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

import log

_logger = log.get_logger("parts_bin.web_search")

_DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

# DDG result links look like: /l/?uddg=<url-encoded-actual-url>&...
_DDG_REDIRECT_RE = re.compile(r'href="/l/\?[^"]*uddg=([^&"]+)', re.IGNORECASE)


def _extract_urls_from_ddg_html(html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _DDG_REDIRECT_RE.finditer(html):
        raw = unquote(match.group(1))
        # Sometimes doubly-encoded
        if raw.startswith("http") is False:
            raw = unquote(raw)
        if raw.startswith("http") and raw not in seen:
            seen.add(raw)
            urls.append(raw)
    return urls


async def search_datasheet_pdfs(
    part_number: str,
    client: httpx.AsyncClient,
    max_results: int = 5,
) -> list[str]:
    """
    Search DuckDuckGo for datasheet PDFs for the given part number.

    Returns a list of PDF URLs (up to max_results), in result order.
    Returns an empty list on any failure.
    """
    query = f"{part_number} datasheet filetype:pdf"
    try:
        resp = await client.get(
            _DDG_URL,
            params={"q": query},
            headers=_DDG_HEADERS,
            timeout=10.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        all_urls = _extract_urls_from_ddg_html(resp.text)
        pdf_urls = [u for u in all_urls if u.lower().endswith(".pdf")][:max_results]
        _logger.debug("web search results", extra={
            "part_number": part_number,
            "total_urls": len(all_urls),
            "pdf_urls_found": len(pdf_urls),
        })
        return pdf_urls
    except Exception as exc:
        _logger.warning("web search failed", extra={
            "part_number": part_number,
            "error": str(exc),
        })
        return []
