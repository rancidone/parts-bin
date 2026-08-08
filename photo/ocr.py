"""
Local OCR helpers for label photos.

Uses the `tesseract` CLI when available, then applies a conservative
confidence/signal gate so we only skip vision calls when the extracted text
looks actionable for parts inventory extraction.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from photo.pipeline import preprocess

_PACKAGE_RE = re.compile(
    r"\b(?:\d{4}|\d{5}|SOT-?\d+(?:-\d+)?|SOIC-?\d+|TSSOP-?\d+|MSOP-?\d+|SSOP-?\d+|"
    r"QFN-?\d+|DFN-?\d+|LQFP-?\d+|TQFP-?\d+|QFP-?\d+|DIP-?\d+|SOP-?\d+|TO-?\d+|"
    r"LED-SMD|panel-mount|through-hole)\b",
    re.IGNORECASE,
)
_PASSIVE_VALUE_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*(?:R|K|M|G|OHM|OHMS|PF|NF|UF|uF|µF|MH|UH|uH|µH|NH|F|H)|\d+[RrKkMmGg]\d+)\b"
)
_PART_NUMBER_RE = re.compile(r"\b(?=[A-Z0-9-]{5,}\b)(?=.*\d)[A-Z][A-Z0-9-]{4,}\b")
_QUANTITY_RE = re.compile(r"\b(?:qty|quantity|q'ty)\s*[:=]?\s*\d+\b|\b\d+\s*(?:pcs|pieces|ea)\b", re.IGNORECASE)


@dataclass
class OCRResult:
    engine: str
    status: str
    text: str
    average_confidence: float | None
    signal_count: int
    should_use_text_only: bool


def _normalize_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _score_text(text: str, average_confidence: float | None) -> tuple[int, bool]:
    if not text:
        return 0, False

    signals = 0
    if _PART_NUMBER_RE.search(text):
        signals += 2
    if _PACKAGE_RE.search(text):
        signals += 1
    if _PASSIVE_VALUE_RE.search(text):
        signals += 1
    if _QUANTITY_RE.search(text):
        signals += 1

    long_alnum_tokens = [token for token in re.findall(r"[A-Z0-9-]{5,}", text.upper()) if any(ch.isdigit() for ch in token)]
    if len(long_alnum_tokens) >= 2:
        signals += 1

    if average_confidence is not None and average_confidence < 45:
        return signals, False
    if len(text) < 8:
        return signals, False
    return signals, signals >= 2


def extract_local_ocr(raw_bytes: bytes) -> OCRResult:
    binary = shutil.which("tesseract")
    if not binary:
        return OCRResult(
            engine="tesseract",
            status="unavailable",
            text="",
            average_confidence=None,
            signal_count=0,
            should_use_text_only=False,
        )

    try:
        jpeg_b64 = preprocess(raw_bytes)
    except ValueError:
        return OCRResult(
            engine="tesseract",
            status="invalid_image",
            text="",
            average_confidence=None,
            signal_count=0,
            should_use_text_only=False,
        )

    import base64

    image_bytes = base64.b64decode(jpeg_b64)
    with tempfile.TemporaryDirectory(prefix="parts-bin-ocr-") as tmpdir:
        image_path = Path(tmpdir) / "label.jpg"
        image_path.write_bytes(image_bytes)

        try:
            proc = subprocess.run(
                [binary, str(image_path), "stdout", "--psm", "6", "tsv"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return OCRResult(
                engine="tesseract",
                status="error",
                text="",
                average_confidence=None,
                signal_count=0,
                should_use_text_only=False,
            )

    words: list[str] = []
    confidences: list[float] = []
    reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        words.append(text)
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1
        if conf >= 0:
            confidences.append(conf)

    text = _normalize_text(" ".join(words))
    avg_conf = round(sum(confidences) / len(confidences), 1) if confidences else None
    signal_count, use_text_only = _score_text(text, avg_conf)
    status = "ok" if text else "empty"
    return OCRResult(
        engine="tesseract",
        status=status,
        text=text,
        average_confidence=avg_conf,
        signal_count=signal_count,
        should_use_text_only=use_text_only,
    )
