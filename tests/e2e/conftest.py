"""Live OpenAI checks require explicit opt-in and isolated test data."""

import os
import pytest

requires_agent_smoke = pytest.mark.skipif(
    os.environ.get("PARTS_BIN_SMOKE_RUNTIME") != "openai"
    or not os.environ.get("OPENAI_API_KEY")
    or not os.environ.get("PARTS_BIN_OPENAI_MODEL"),
    reason="Set PARTS_BIN_SMOKE_RUNTIME=openai, OPENAI_API_KEY, and PARTS_BIN_OPENAI_MODEL to opt in",
)
