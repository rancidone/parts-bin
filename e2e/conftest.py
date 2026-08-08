"""Opt-in live agent-runtime smoke-test configuration."""

import os

import pytest


SMOKE_RUNTIME = os.environ.get("PARTS_BIN_SMOKE_RUNTIME", "").strip().lower()


def smoke_configured() -> bool:
    if SMOKE_RUNTIME == "local":
        return bool(os.environ.get("PARTS_BIN_AGENT_LOCAL_BASE_URL"))
    if SMOKE_RUNTIME == "codex":
        return bool(os.environ.get("PARTS_BIN_AGENT_CODEX_COMMAND"))
    return False


requires_agent_smoke = pytest.mark.skipif(
    not smoke_configured(),
    reason="Set PARTS_BIN_SMOKE_RUNTIME and its runtime configuration to opt in",
)
