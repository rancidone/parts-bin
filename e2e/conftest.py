"""
E2E test configuration.

These tests require a live llama.cpp server. They are skipped automatically
if PARTS_BIN_LLM_BASE_URL is not set OR if the server is unreachable.

Set it to match your llama.cpp server, e.g.:
  export PARTS_BIN_LLM_BASE_URL=http://woody.brownfamily.house:8080/v1
  export PARTS_BIN_LLM_MODEL=unsloth/Qwen3.5-9B-GGUF:Q4_K_M   # optional

Run with:
  uv run pytest e2e/ -v
"""

import os

import httpx
import pytest

from db.persistence import init_db
from llm.client import LLMClient

# ---------------------------------------------------------------------------
# Skip guard — skipped if no URL configured or server is unreachable.
# ---------------------------------------------------------------------------

LLM_BASE_URL = os.environ.get("PARTS_BIN_LLM_BASE_URL", "")
LLM_MODEL    = os.environ.get("PARTS_BIN_LLM_MODEL", "qwen")


def _llm_reachable() -> bool:
    if not LLM_BASE_URL:
        return False
    # Probe the base URL with a short timeout.
    probe = LLM_BASE_URL.rstrip("/")
    # Strip /v1 suffix to hit the server root.
    if probe.endswith("/v1"):
        probe = probe[:-3]
    try:
        httpx.get(probe, timeout=3.0)
        return True
    except Exception:
        return False


_llm_available: bool | None = None


def llm_available() -> bool:
    global _llm_available
    if _llm_available is None:
        _llm_available = _llm_reachable()
    return _llm_available


requires_llm = pytest.mark.skipif(
    not llm_available(),
    reason="LLM server not configured or unreachable — skipping E2E tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "e2e.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def llm():
    return LLMClient(base_url=LLM_BASE_URL, model=LLM_MODEL, timeout=120.0)
