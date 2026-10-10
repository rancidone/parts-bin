"""Opt-in OpenAI smoke check. Uses a temporary database and may incur API costs."""

import os
import pytest
from db.repository import SQLitePartsBinRepository

from agent_runtime import ApprovalEngine, ConversationStore, OpenAIResponsesRuntime, OpenAIResponsesTransport
from domain import PartsBinService
from tools import PartsBinToolRegistry
from .conftest import requires_agent_smoke


@pytest.mark.asyncio
@requires_agent_smoke
async def test_configured_runtime_answers_with_normalized_events(tmp_path):
    transport = OpenAIResponsesTransport(api_key=os.environ["OPENAI_API_KEY"], model=os.environ["PARTS_BIN_OPENAI_MODEL"])
    repository = SQLitePartsBinRepository(tmp_path / "parts.db")
    runtime = OpenAIResponsesRuntime(transport,
        registry=PartsBinToolRegistry(PartsBinService(repository)),
        store=ConversationStore(tmp_path / "conversations.db"), approvals=ApprovalEngine(repository))
    try:
        result = await runtime.run("smoke", "Reply with a short greeting and do not use tools.")
        assert result.status == "completed"
        assert any(event.kind == "assistant_text" for event in result.events)
        assert result.events[-1].kind == "completed"
    finally:
        await transport.close()
