"""Tests for local OCR routing and ephemeral image handling."""

import io
import subprocess
from unittest.mock import Mock

import pytest
from PIL import Image

from photo.ocr import OCRResult, _score_text, extract_local_ocr


@pytest.fixture
def label_bytes():
    image = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(image, format="PNG")
    return image.getvalue()


def test_ocr_pipes_preprocessed_image_and_reads_utf8(monkeypatch, label_bytes):
    monkeypatch.setattr("photo.ocr.shutil.which", lambda _: "/bin/tesseract")
    run = Mock(return_value=subprocess.CompletedProcess(
        [], 0, stdout="conf\ttext\n90\tRC0402FR-0710KL\n80\t10kΩ\n-1\tqty\n90\t20\n".encode(), stderr=b""))
    monkeypatch.setattr("photo.ocr.subprocess.run", run)

    result = extract_local_ocr(label_bytes)

    args, kwargs = run.call_args
    assert args[0] == ["/bin/tesseract", "stdin", "stdout", "--psm", "6", "tsv"]
    assert isinstance(kwargs["input"], bytes)
    with Image.open(io.BytesIO(kwargs["input"])) as image:
        assert image.format == "JPEG"
        assert image.size == (80, 40)
    assert result.status == "ok"
    assert result.text == "RC0402FR-0710KL 10kΩ qty 20"
    assert result.average_confidence == 86.7
    assert result.should_use_text_only is True


@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.CalledProcessError(1, "tesseract")])
def test_ocr_process_failure_uses_vision(monkeypatch, label_bytes, error):
    monkeypatch.setattr("photo.ocr.shutil.which", lambda _: "/bin/tesseract")
    monkeypatch.setattr("photo.ocr.subprocess.run", Mock(side_effect=error))

    result = extract_local_ocr(label_bytes)

    assert result.status == "error"
    assert result.text == ""
    assert result.should_use_text_only is False


@pytest.mark.parametrize("available, image, status", [
    (False, b"unused", "unavailable"),
    (True, b"invalid", "invalid_image"),
])
def test_ocr_skips_process_without_valid_input(monkeypatch, available, image, status):
    monkeypatch.setattr("photo.ocr.shutil.which", lambda _: "/bin/tesseract" if available else None)
    run = Mock()
    monkeypatch.setattr("photo.ocr.subprocess.run", run)

    result = extract_local_ocr(image)

    run.assert_not_called()
    assert result.status == status
    assert result.should_use_text_only is False


class TestScoreText:
    def test_recognizes_actionable_label_text(self):
        signal_count, should_use_text_only = _score_text(
            "RC0402FR-0710KL 10k 0402 qty 20",
            84.0,
        )

        assert signal_count >= 2
        assert should_use_text_only is True

    def test_rejects_low_confidence_noise(self):
        signal_count, should_use_text_only = _score_text(
            "1ok o4o2 maybe",
            22.0,
        )

        assert signal_count >= 0
        assert should_use_text_only is False


class TestOCRResult:
    def test_result_shape(self):
        result = OCRResult(
            engine="tesseract",
            status="ok",
            text="10k 0402",
            average_confidence=75.0,
            signal_count=2,
            should_use_text_only=True,
        )

        assert result.engine == "tesseract"
        assert result.should_use_text_only is True
