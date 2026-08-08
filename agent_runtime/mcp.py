"""Small in-process MCP client useful to Codex hosts and deterministic tests."""

from __future__ import annotations

from typing import Any

from tools.mcp_server import MCPServer


class PartsBinMCPClient:
    def __init__(self, server: MCPServer):
        self.server = server
        self._id = 0

    async def call_tool(self, name: str, arguments: dict[str, Any], *, approval: Any | None = None) -> dict[str, Any]:
        self._id += 1
        response = await self.server.handle({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments,
                       "_meta": {"parts_bin_approval": None if approval is None else {
                           "tool_name": approval.tool_name, "arguments_fingerprint": approval.arguments_fingerprint}}}})
        assert response is not None
        return response["result"]["structuredContent"]
