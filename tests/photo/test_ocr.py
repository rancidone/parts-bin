"""Tests for local OCR routing heuristics."""

from photo.ocr import OCRResult, _score_text


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
