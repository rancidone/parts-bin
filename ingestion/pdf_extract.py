"""
Bounded extraction helpers for API-derived PDFs.

Uses pdfminer.six for proper compressed PDF text extraction, then applies
label-matching regexes to find structured fields.
"""

from __future__ import annotations

import io
import re

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTAnno, LTChar, LTTextContainer

TARGET_FIELDS = {"manufacturer", "description", "package", "part_number"}

_WS_RE = re.compile(r"\s+")
_NEXT_LABEL = (
    r"(?=\s+(?:Manufacturer(?: Part Number)?|Part Number|MPN|Package(?:\s*/\s*Case)?|"
    r"Supplier Device Package|Description|endstream|endobj)\b(?:\s*[:\-])?|$)"
)
_LABEL_PATTERNS = {
    "manufacturer": re.compile(
        rf"Manufacturer\s*[:\-]\s*([A-Za-z0-9&.,()\/ +_-]{{2,120}}?){_NEXT_LABEL}",
        re.IGNORECASE,
    ),
    "part_number": re.compile(
        rf"(?:Manufacturer Part Number|Part Number|MPN)\s*[:\-]\s*([A-Za-z0-9._\/+\-]{{2,80}}?){_NEXT_LABEL}",
        re.IGNORECASE,
    ),
    "package": re.compile(
        rf"(?:Package(?:\s*/\s*Case)?|Supplier Device Package)\s*[:\-]\s*([A-Za-z0-9.,()\/ +_-]{{2,120}}?){_NEXT_LABEL}",
        re.IGNORECASE,
    ),
    "description": re.compile(
        rf"Description\s*[:\-]\s*([A-Za-z0-9&.,()\/ +_-]{{8,200}}?){_NEXT_LABEL}",
        re.IGNORECASE,
    ),
}

# Only scan the first N pages — product info is always near the front.
_MAX_PAGES = 4


def _extract_text_pages(pdf_bytes: bytes) -> list[str]:
    """Extract per-page text from PDF bytes using pdfminer."""
    pages: list[str] = []
    try:
        for page_layout in extract_pages(io.BytesIO(pdf_bytes), maxpages=_MAX_PAGES):
            parts: list[str] = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    for text_line in element:
                        line_chars: list[str] = []
                        for char in text_line:
                            if isinstance(char, (LTChar, LTAnno)):
                                line_chars.append(char.get_text())
                        parts.append("".join(line_chars))
            pages.append("".join(parts))
    except Exception:
        # Fall back to raw decode if pdfminer fails (e.g. encrypted or corrupt PDF).
        pages = pdf_bytes.decode("latin-1", errors="ignore").split("\x0c")[:_MAX_PAGES]
    return pages


def extract_pdf_candidates(pdf_bytes: bytes) -> dict[str, dict]:
    pages = _extract_text_pages(pdf_bytes)
    multiple_pages = len(pages) > 1
    candidates: dict[str, dict] = {}
    for page_idx, raw_page in enumerate(pages):
        normalized = _WS_RE.sub(" ", raw_page)
        for field_name, pattern in _LABEL_PATTERNS.items():
            if field_name in candidates:
                continue
            match = pattern.search(normalized)
            if not match:
                continue
            value = _clean_value(match.group(1))
            if value:
                candidates[field_name] = {
                    "value": value,
                    "evidence": _truncate_evidence(match.group(0)),
                    "method": "pdf-labeled-text",
                    "page_ref": page_idx + 1 if multiple_pages else None,
                }
    return candidates


def _clean_value(value: str) -> str:
    cleaned = _WS_RE.sub(" ", value).strip()
    cleaned = cleaned.rstrip(")>/]")
    return cleaned.strip()


def _truncate_evidence(value: str, limit: int = 160) -> str:
    compact = _WS_RE.sub(" ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
