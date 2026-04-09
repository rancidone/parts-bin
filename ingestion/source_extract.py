"""
Bounded extraction helpers for API-derived product pages.

This module intentionally stays narrow:
- classify fetched content
- select a provider-aware HTML extractor
- extract only known inventory fields from structured HTML
"""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

TARGET_FIELDS = {"manufacturer", "description", "package", "part_number"}

_SCRIPT_JSON_LD_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_META_TAG_RE = re.compile(r"<meta\s+([^>]+)>", re.IGNORECASE)
_META_ATTR_RE = re.compile(r'([a-zA-Z:_-]+)\s*=\s*["\'](.*?)["\']', re.DOTALL)
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<(?:th|td)[^>]*>(.*?)</(?:th|td)>\s*<(?:th|td)[^>]*>(.*?)</(?:th|td)>\s*</tr>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_GENERIC_LABEL_FIELD_MAP = {
    "manufacturer": "manufacturer",
    "mfr": "manufacturer",
    "manufacturer part number": "part_number",
    "manufacturer product number": "part_number",
    "mpn": "part_number",
    "product description": "description",
    "description": "description",
    "package": "package",
    "package / case": "package",
    "package case": "package",
}

_PROVIDER_LABEL_FIELD_MAP = {
    "digikey": {
        **_GENERIC_LABEL_FIELD_MAP,
        "supplier device package": "package",
        "detailed description": "description",
    },
    "lcsc": {
        **_GENERIC_LABEL_FIELD_MAP,
        "encapsulation": "package",
        "package/ case": "package",
        "manufacturer": "manufacturer",
    },
}


def classify_content(content_type: str | None, body: str) -> str:
    normalized_type = (content_type or "").lower()
    sample = body.lstrip()[:256].lower()

    if "text/html" in normalized_type:
        return "structured_html_product_page"
    if "application/pdf" in normalized_type or sample.startswith("%pdf"):
        return "pdf_document"
    if sample.startswith("<!doctype html") or sample.startswith("<html") or "<head" in sample:
        return "structured_html_product_page"
    return "unsupported_content"


def detect_provider(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if "digikey." in host:
        return "digikey"
    if "lcsc." in host:
        return "lcsc"
    return None


def extract_html_fields(url: str, body: str) -> dict:
    return {field_name: item["value"] for field_name, item in extract_html_candidates(url, body).items()}


def extract_html_candidates(url: str, body: str) -> dict[str, dict]:
    provider = detect_provider(url)
    candidates: dict[str, dict] = {}

    _merge_candidates(candidates, _extract_json_ld_candidates(body))
    _merge_candidates(candidates, _extract_meta_description_candidate(body))

    if provider in {"digikey", "lcsc"}:
        _merge_candidates(candidates, _extract_labeled_row_candidates(body, provider))

    return {
        field_name: candidate
        for field_name, candidate in candidates.items()
        if field_name in TARGET_FIELDS and candidate.get("value")
    }


def _merge_candidates(destination: dict[str, dict], source: dict[str, dict]) -> None:
    for field_name, candidate in source.items():
        if field_name not in destination:
            destination[field_name] = candidate


def _extract_json_ld_candidates(body: str) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for raw_json in _SCRIPT_JSON_LD_RE.findall(body):
        parsed = _safe_json_load(raw_json)
        for item in _iter_json_ld_items(parsed):
            _merge_candidates(candidates, _candidates_from_json_ld_item(item))
    return candidates


def _extract_meta_description_candidate(body: str) -> dict[str, dict]:
    for attr_blob in _META_TAG_RE.findall(body):
        attrs = {name.lower(): unescape(value).strip() for name, value in _META_ATTR_RE.findall(attr_blob)}
        key = attrs.get("name") or attrs.get("property")
        if key in {"description", "og:description"} and attrs.get("content"):
            content = attrs["content"]
            return {
                "description": {
                    "value": content,
                    "evidence": _truncate_evidence(content),
                    "method": "html-meta-description",
                }
            }
    return {}


def _extract_labeled_row_candidates(body: str, provider: str) -> dict[str, dict]:
    label_map = _PROVIDER_LABEL_FIELD_MAP[provider]
    candidates: dict[str, dict] = {}
    for raw_label, raw_value in _ROW_RE.findall(body):
        label_text = _strip_tags(raw_label)
        label = _normalize_label(label_text)
        field_name = label_map.get(label)
        if not field_name:
            continue
        value = _strip_tags(raw_value)
        if value:
            candidates[field_name] = {
                "value": value,
                "evidence": _truncate_evidence(f"{label_text}: {value}"),
                "method": f"{provider}-html-labeled-row",
            }
    return candidates


def _safe_json_load(raw_json: str):
    cleaned = unescape(raw_json).strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _iter_json_ld_items(payload):
    if payload is None:
        return
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_json_ld_items(item)
        return
    if isinstance(payload, dict):
        if isinstance(payload.get("@graph"), list):
            for item in payload["@graph"]:
                yield from _iter_json_ld_items(item)
        yield payload


def _candidates_from_json_ld_item(item: dict) -> dict[str, dict]:
    candidates: dict[str, dict] = {}

    manufacturer = item.get("manufacturer")
    if isinstance(manufacturer, dict):
        manufacturer = manufacturer.get("name")
    if isinstance(manufacturer, str) and manufacturer.strip():
        value = manufacturer.strip()
        candidates["manufacturer"] = {
            "value": value,
            "evidence": _truncate_evidence(value),
            "method": "json-ld-manufacturer",
        }

    for key in ("description", "mpn", "sku", "name"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        stripped = value.strip()
        if key == "description" and "description" not in candidates:
            candidates["description"] = {
                "value": stripped,
                "evidence": _truncate_evidence(stripped),
                "method": "json-ld-description",
            }
        elif key in {"mpn", "sku"} and "part_number" not in candidates:
            candidates["part_number"] = {
                "value": stripped,
                "evidence": _truncate_evidence(f"{key}: {stripped}"),
                "method": f"json-ld-{key}",
            }

    additional = item.get("additionalProperty")
    if isinstance(additional, list):
        for prop in additional:
            if not isinstance(prop, dict):
                continue
            label = _normalize_label(str(prop.get("name", "")))
            field_name = _GENERIC_LABEL_FIELD_MAP.get(label)
            value = str(prop.get("value", "")).strip()
            if field_name in TARGET_FIELDS and value and field_name not in candidates:
                candidates[field_name] = {
                    "value": value,
                    "evidence": _truncate_evidence(f"{prop.get('name', '')}: {value}"),
                    "method": "json-ld-additional-property",
                }

    return candidates


def _truncate_evidence(value: str, limit: int = 160) -> str:
    compact = _WS_RE.sub(" ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _strip_tags(value: str) -> str:
    text = _TAG_RE.sub(" ", unescape(value))
    return _WS_RE.sub(" ", text).strip()


def _normalize_label(value: str) -> str:
    return _WS_RE.sub(" ", value).strip().lower().rstrip(":")
