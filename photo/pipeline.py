"""
Photo preprocessing pipeline.

preprocess(raw_bytes) → base64-encoded JPEG string ready for LLM handoff.

Steps:
  1. Decode image bytes (JPEG, PNG, WEBP accepted)
  2. Resize to max 1024px longest side, preserving aspect ratio
  3. Re-encode as JPEG @ quality 85
  4. Base64-encode
"""

import base64
import io

from PIL import Image

MAX_LONGEST_SIDE = 1024
JPEG_QUALITY = 85
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def preprocess(raw_bytes: bytes) -> str:
    """
    Preprocess raw image bytes and return a base64-encoded JPEG string.

    Raises ValueError if the input exceeds MAX_UPLOAD_BYTES or cannot be decoded.
    """
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Image too large: {len(raw_bytes)} bytes (max {MAX_UPLOAD_BYTES})")

    try:
        img = Image.open(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise ValueError(f"Cannot decode image: {exc}") from exc

    # Convert to RGB — JPEG doesn't support alpha or palette modes.
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Resize to fit within MAX_LONGEST_SIDE x MAX_LONGEST_SIDE.
    w, h = img.size
    longest = max(w, h)
    if longest > MAX_LONGEST_SIDE:
        scale = MAX_LONGEST_SIDE / longest
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)

    # Encode to JPEG in memory.
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    buf.seek(0)

    return base64.b64encode(buf.read()).decode()
