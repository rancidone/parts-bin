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

# Known package designators for inline-prose extraction (e.g. "in SOT-23 5-Pin Package").
_PACKAGE_NAMES = (
    r"SOT-\d+[A-Z0-9\-]*"
    r"|QFN-?\d*[A-Z0-9\-]*"
    r"|TSSOP-?\d*"
    r"|SOIC-?\d*[A-Z0-9\-]*"
    r"|DFN-?\d*[A-Z0-9\-]*"
    r"|WSON-?\d*"
    r"|TO-\d+[A-Z0-9\-]*"
    r"|LGA-?\d*"
    r"|BGA-?\d*"
    r"|LFCSP-?\d*"
    r"|MSOP-?\d*"
    r"|TQFP-?\d*"
    r"|LQFP-?\d*"
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
    "description": re.compile(
        rf"Description\s*[:\-]\s*([A-Za-z0-9&.,()\/ +_-]{{8,200}}?){_NEXT_LABEL}",
        re.IGNORECASE,
    ),
}

_PART_NUMBER_SCAN_RE = re.compile(
    r"(?:Manufacturer Part Number|Part Number|MPN)\s*[:\-]\s*([A-Za-z0-9._\/+\-]{2,80})",
    re.IGNORECASE,
)

# Package patterns tried in priority order (first match with a value wins).
_PACKAGE_PATTERNS: list[re.Pattern] = [
    # DigiKey/distributor labeled row: "Package / Case: SOT-23-5"
    re.compile(
        rf"(?:Package(?:\s*/\s*Case)?|Supplier Device Package)\s*[:\-]\s*([A-Za-z0-9.,()\/ +_-]{{2,120}}?){_NEXT_LABEL}",
        re.IGNORECASE,
    ),
    # TI Device Information table: "PACKAGE BODY SIZE (NOM) SOT-23 (5)"
    re.compile(
        rf"PACKAGE\s+BODY\s+SIZE\s*\([^)]+\)\s+({_PACKAGE_NAMES})(\s*\(\d+\))?",
        re.IGNORECASE,
    ),
    # Inline prose: "available in SOT-23 5-Pin Package" / "in a SOT-23 package"
    re.compile(
        rf"(?:available in|in)\s+(?:a\s+)?({_PACKAGE_NAMES})(?:\s+\d+-[Pp]in)?",
        re.IGNORECASE,
    ),
]

# Only scan the first N pages — product info is always near the front.
_MAX_PAGES = 4


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


def _find_labeled_part_numbers(normalized_pages: list[str]) -> list[str]:
    values: list[str] = []
    for page in normalized_pages:
        for match in _PART_NUMBER_SCAN_RE.finditer(page):
            value = _clean_value(match.group(1))
            if value:
                values.append(value)
    return _dedupe_preserve_order(values)


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


def _match_package(normalized: str) -> tuple[str, str] | None:
    """Try package patterns in priority order; return (value, evidence) or None."""
    for pattern in _PACKAGE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        # Groups: first non-None group is the package name; optional pin-count group may follow.
        groups = [g for g in match.groups() if g is not None]
        if not groups:
            continue
        # Combine base name + pin-count suffix if present (e.g. "SOT-23" + " (5)")
        value = _clean_value("".join(groups))
        if value:
            return value, _truncate_evidence(match.group(0))
    return None


def extract_pdf_candidates(pdf_bytes: bytes) -> dict[str, dict]:
    pages = _extract_text_pages(pdf_bytes)
    multiple_pages = len(pages) > 1
    normalized_pages = [_WS_RE.sub(" ", raw_page) for raw_page in pages]
    labeled_part_numbers = _find_labeled_part_numbers(normalized_pages)
    has_variant_ambiguity = len(labeled_part_numbers) > 1
    candidates: dict[str, dict] = {}
    for page_idx, normalized in enumerate(normalized_pages):
        for field_name, pattern in _LABEL_PATTERNS.items():
            if field_name in candidates:
                continue
            match = pattern.search(normalized)
            if not match:
                continue
            value = _clean_value(match.group(1))
            if value:
                ambiguous = has_variant_ambiguity and field_name in {"part_number", "package", "description"}
                candidates[field_name] = {
                    "value": value,
                    "evidence": _truncate_evidence(match.group(0)),
                    "method": "pdf-labeled-text",
                    "page_ref": page_idx + 1 if multiple_pages else None,
                    "ambiguous": ambiguous,
                }
        if "package" not in candidates:
            result = _match_package(normalized)
            if result:
                value, evidence = result
                ambiguous = has_variant_ambiguity
                candidates["package"] = {
                    "value": value,
                    "evidence": evidence,
                    "method": "pdf-labeled-text",
                    "page_ref": page_idx + 1 if multiple_pages else None,
                    "ambiguous": ambiguous,
                }
    return candidates


def _clean_value(value: str) -> str:
    cleaned = _WS_RE.sub(" ", value).strip()
    # Strip trailing PDF syntax noise, but only if parens are unbalanced.
    while cleaned and cleaned[-1] in ">/]":
        cleaned = cleaned[:-1].strip()
    if cleaned.endswith(")") and cleaned.count("(") < cleaned.count(")"):
        cleaned = cleaned[:-1].strip()
    return cleaned


def _truncate_evidence(value: str, limit: int = 160) -> str:
    compact = _WS_RE.sub(" ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
