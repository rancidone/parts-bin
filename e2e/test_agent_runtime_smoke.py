"""Live smoke test; never required for the normal test suite.

Local:
  PARTS_BIN_SMOKE_RUNTIME=local
  PARTS_BIN_AGENT_LOCAL_BASE_URL=http://localhost:8080/v1
  PARTS_BIN_AGENT_LOCAL_MODEL=local

Codex:
  PARTS_BIN_SMOKE_RUNTIME=codex
  PARTS_BIN_AGENT_CODEX_COMMAND='...'
"""

import os

import pytest

from agent_runtime import (
    ApprovalEngine,
    CodexAppServerRuntime,
    CodexAppServerTransport,
    ConversationStore,
    LocalOpenAICompatibleRuntime,
    LocalOpenAICompatibleTransport,
    PartsBinMCPClient,
)
from agent_runtime.runtime import AgentRuntime
from domain import PartsBinService
from tools import PartsBinToolRegistry
from tools.mcp_server import MCPServer

from .conftest import SMOKE_RUNTIME, requires_agent_smoke


def _runtime(tmp_path) -> AgentRuntime:
    store = ConversationStore(tmp_path / "conversations.db")
    registry = PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db"))
    common = {"registry": registry, "store": store, "approvals": ApprovalEngine()}
    if SMOKE_RUNTIME == "local":
        return LocalOpenAICompatibleRuntime(
            LocalOpenAICompatibleTransport(
                base_url=os.environ["PARTS_BIN_AGENT_LOCAL_BASE_URL"],
                model=os.environ.get("PARTS_BIN_AGENT_LOCAL_MODEL", "local"),
            ),
            supports_native_tools=True,
            **common,
        )
    return CodexAppServerRuntime(
        CodexAppServerTransport(command=os.environ["PARTS_BIN_AGENT_CODEX_COMMAND"]),
        mcp_client=PartsBinMCPClient(MCPServer(registry)),
        **common,
    )


@pytest.mark.asyncio
@requires_agent_smoke
async def test_configured_runtime_answers_with_normalized_events(tmp_path):
    result = await _runtime(tmp_path).run("smoke", "Reply with a short greeting and do not use tools.")
    assert result.status == "completed"
    assert any(event.kind == "assistant_text" for event in result.events)
    assert result.events[-1].kind == "completed"
