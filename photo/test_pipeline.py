"""Tests for photo preprocessing pipeline."""

import base64
import io

import pytest
from PIL import Image

from photo.pipeline import MAX_LONGEST_SIDE, MAX_UPLOAD_BYTES, preprocess


def _make_jpeg(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_rgba(width: int, height: int) -> bytes:
    img = Image.new("RGBA", (width, height), color=(10, 20, 30, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestPreprocess:
    def test_returns_valid_base64(self):
        raw = _make_jpeg(200, 200)
        result = preprocess(raw)
        # Should be decodable base64.
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_output_is_jpeg(self):
        raw = _make_jpeg(200, 200)
        result = preprocess(raw)
        decoded = base64.b64decode(result)
        img = Image.open(io.BytesIO(decoded))
        assert img.format == "JPEG"

    def test_small_image_not_upscaled(self):
        raw = _make_jpeg(100, 100)
        result = preprocess(raw)
        decoded = base64.b64decode(result)
        img = Image.open(io.BytesIO(decoded))
        assert img.size == (100, 100)

    def test_large_image_resized_landscape(self):
        raw = _make_jpeg(2000, 1000)
        result = preprocess(raw)
        decoded = base64.b64decode(result)
        img = Image.open(io.BytesIO(decoded))
        w, h = img.size
        assert max(w, h) == MAX_LONGEST_SIDE
        # Aspect ratio preserved (within 1px rounding).
        assert abs(w / h - 2.0) < 0.02

    def test_large_image_resized_portrait(self):
        raw = _make_jpeg(800, 2400)
        result = preprocess(raw)
        decoded = base64.b64decode(result)
        img = Image.open(io.BytesIO(decoded))
        w, h = img.size
        assert max(w, h) == MAX_LONGEST_SIDE

    def test_rgba_png_converted_to_rgb_jpeg(self):
        raw = _make_png_rgba(200, 200)
        result = preprocess(raw)
        decoded = base64.b64decode(result)
        img = Image.open(io.BytesIO(decoded))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_too_large_raises(self):
        oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
        with pytest.raises(ValueError, match="too large"):
            preprocess(oversized)

    def test_invalid_bytes_raises(self):
        with pytest.raises(ValueError, match="Cannot decode"):
            preprocess(b"not an image")
