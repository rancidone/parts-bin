---
status: draft
last_updated: 2026-04-12
---
# Design Unit: Photo Pipeline

## Problem
Users upload photos of parts (labels, reels, bags) on mobile (camera) or desktop (file picker). The photo must reach the LLM for label text and visual context extraction. Transport, preprocessing, and LLM handoff are designed here.

## Decision: Vision-Capable Model
Qwen 3.5 via llama.cpp supports vision. Photos are passed directly to the LLM as base64-encoded image content parts in the OpenAI-compatible chat completions API — no OCR step required.

## Pipeline

1. **Client upload** — multipart POST to `/chat` with an optional `photo` file field alongside the `message` text field. Accepted formats: JPEG, PNG, WEBP.
2. **Server preprocessing** — on receipt, the server:
   - Decodes the uploaded bytes into an image
   - Resizes to a maximum longest-side of 1024px (preserving aspect ratio) using Pillow
   - Re-encodes as JPEG at quality 85
   - Base64-encodes the result
3. **LLM handoff** — the preprocessed image is included in the chat message as an `image_url` content part with a `data:image/jpeg;base64,...` URI, alongside the text content part. This is the standard OpenAI multimodal message format, which llama.cpp supports.
4. **No persistence** — images are processed in memory and discarded. Nothing is written to disk.

## Constraints
- Mobile: camera capture → JPEG. Desktop: file picker → JPEG/PNG/WEBP.
- Preprocessing happens server-side; client sends raw bytes.
- Both photo and text may be present in the same turn.
- Max upload size: 10 MB (enforced at server boundary before preprocessing).

## What This Unit Does Not Cover
- Prompt content for extraction (see LLM Integration)
- Server routing and multipart handling (see Server/API)