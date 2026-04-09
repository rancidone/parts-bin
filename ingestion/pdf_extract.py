"""
Bounded extraction helpers for API-derived PDFs.

This is intentionally conservative and only supports text-based PDFs where
useful labels are visible in the raw stream text after a latin-1 decode.
"""

from __future__ import annotations

import re

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


def extract_pdf_candidates(pdf_bytes: bytes) -> dict[str, dict]:
    decoded = pdf_bytes.decode("latin-1", errors="ignore")
    pages = decoded.split("\x0c")
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


def _normalize_pdf_text(pdf_bytes: bytes) -> str:
    decoded = pdf_bytes.decode("latin-1", errors="ignore")
    return _WS_RE.sub(" ", decoded)


def _clean_value(value: str) -> str:
    cleaned = _WS_RE.sub(" ", value).strip()
    cleaned = cleaned.rstrip(")>/]")
    return cleaned.strip()


def _truncate_evidence(value: str, limit: int = 160) -> str:
    compact = _WS_RE.sub(" ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
