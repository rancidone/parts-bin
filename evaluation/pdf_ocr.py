"""Bounded raster/OCR experiment, deliberately separate from production extraction."""

import csv
import hashlib
import io
import shutil
import subprocess
from dataclasses import dataclass

import pdfplumber

from ingestion.errors import EnrichmentError
from ingestion.supplied_source import MAX_BYTES, MAX_PAGES, MAX_TEXT_CHARS

MAX_OCR_PAGES = 3
MAX_PIXEL_COUNT = 10_000_000


@dataclass(frozen=True)
class OCRExperiment:
    pages: tuple[str, ...]
    measurements: tuple[dict, ...]


def raster_ocr(data: bytes, page_numbers: list[int], *, resolution=300) -> OCRExperiment:
    binary = shutil.which('tesseract')
    if not binary:
        raise EnrichmentError('Tesseract is unavailable for the OCR experiment')
    if (len(data) > MAX_BYTES or not data.startswith(b'%PDF-') or
            not 1 <= len(page_numbers) <= MAX_OCR_PAGES or len(set(page_numbers)) != len(page_numbers) or
            resolution not in (150, 300)):
        raise EnrichmentError('OCR experiment exceeds its input budget')
    measurements = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > MAX_PAGES or any(type(n) is not int or not 1 <= n <= len(pdf.pages) for n in page_numbers):
            raise EnrichmentError('OCR experiment references unavailable pages')
        pages = [''] * len(pdf.pages)
        total = 0
        for number in page_numbers:
            page = pdf.pages[number - 1]
            if page.width * page.height * (resolution / 72) ** 2 > MAX_PIXEL_COUNT:
                raise EnrichmentError('OCR raster exceeds its pixel budget')
            image = page.to_image(resolution=resolution).original
            output = io.BytesIO()
            image.save(output, format='PNG')
            raster = output.getvalue()
            image.close()
            completed = subprocess.run([binary, 'stdin', 'stdout', '--psm', '3', '-l', 'eng', 'tsv'],
                input=raster, capture_output=True, timeout=15, check=True)
            if len(completed.stdout) > 2_000_000:
                raise EnrichmentError('OCR transcript exceeds its output budget')
            text, quality = transcript(completed.stdout.decode('utf-8'))
            total += len(text)
            if total > MAX_TEXT_CHARS:
                raise EnrichmentError('OCR transcript exceeds its text budget')
            pages[number - 1] = text
            measurements.append({'page': number, 'raster_sha256': hashlib.sha256(raster).hexdigest(),
                                 'resolution': resolution, **quality})
    return OCRExperiment(tuple(pages), tuple(measurements))


def transcript(tsv: str) -> tuple[str, dict]:
    lines, confidence, weight, low = {}, 0.0, 0, []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        word = (row.get('text') or '').strip()
        if not word:
            continue
        key = (row['block_num'], row['par_num'], row['line_num'])
        lines.setdefault(key, []).append(word)
        try:
            score = float(row.get('conf', -1))
        except (TypeError, ValueError):
            score = -1
        if score >= 0:
            confidence += score * len(word)
            weight += len(word)
            if score < 80 and len(low) < 40:
                low.append({'text': word, 'confidence': round(score, 1)})
    return '\n'.join(' '.join(words) for words in lines.values()), {
        'ocr_character_weighted_confidence': round(confidence / weight, 1) if weight else None,
        'low_confidence_words': low,
        'note': 'Recognition confidence measures transcription, not source association or electrical correctness.'}
