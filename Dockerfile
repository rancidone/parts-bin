FROM node:22-bookworm-slim AS ui-builder

WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build


FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    && npm install --global @openai/codex@0.147.0 \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY . ./
COPY --from=ui-builder /app/ui/dist ./ui/dist

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && mkdir -p /home/appuser/.codex \
    && chown -R appuser:appuser /app /home/appuser/.codex

USER appuser

EXPOSE 8000

ENV LOG_LEVEL=INFO \
    TELEMETRY_LOG_FILE=/app/data/telemetry.jsonl

VOLUME ["/app/data"]

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
