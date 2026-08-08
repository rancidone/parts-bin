"""Minimal MCP JSON-RPC stdio projection of the canonical tool registry."""

from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from domain import DomainError, ErrorCode, PartsBinService
from ingestion.lookup import fetch_specs_detailed

from .registry import ApprovalReceipt, PartsBinToolRegistry, ToolExecutionContext


class MCPServer:
    def __init__(self, registry: PartsBinToolRegistry):
        self.registry = registry

    async def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None and method.startswith("notifications/"):
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "resources": {}}, "serverInfo": {"name": "parts-bin", "version": "0.1.0"}}
            elif method == "tools/list":
                result = {"tools": self.registry.list_tools()}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                # MCP callers cannot provide approval data. A trusted server
                # approval engine decides out of band, then this transport
                # creates the receipt internally.
                approval = None
                if self.registry.approval_checker is not None:
                    # A trusted server approval engine may obtain the user's
                    # decision out of band, then issue the receipt itself.
                    approval = ApprovalReceipt.issue(name, arguments)
                outcome = await self.registry.execute(
                    name, arguments, context=ToolExecutionContext(approval=approval)
                )
                is_error = not outcome["ok"]
                return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(outcome, separators=(",", ":"))}], "structuredContent": outcome, "isError": is_error}}
            elif method == "resources/list":
                result = {"resources": self.registry.list_resources()}
            elif method == "resources/read":
                uri = (request.get("params") or {}).get("uri")
                result = {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(self.registry.read_resource(uri), separators=(",", ":"))}]}
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except DomainError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": exc.message, "data": {"code": str(exc.code), "details": exc.details}}}
        except (KeyError, TypeError, ValueError) as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}


async def serve_stdio(server: MCPServer, lines: Iterable[str] | None = None) -> None:
    source = sys.stdin if lines is None else lines
    for line in source:
        if not line.strip():
            continue
        response = await server.handle(json.loads(line))
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "parts.db"
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("config.toml")
    service = PartsBinService(db_path, spec_fetcher=_configured_spec_fetcher(config_path))
    registry = PartsBinToolRegistry(service, approval_checker=_interactive_approval)
    asyncio.run(serve_stdio(MCPServer(registry)))


def _configured_spec_fetcher(config_path: Path):
    """Build the supplier adapter without placing configuration in MCP data."""
    config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
    digikey = config.get("digikey", {})
    credentials = (
        {"client_id": digikey["client_id"], "client_secret": digikey["client_secret"]}
        if digikey.get("client_id") and digikey.get("client_secret")
        else None
    )
    jlcparts_path = config.get("jlcparts", {}).get("db_path")
    search_config = config.get("search")

    async def fetcher(part_number: str) -> dict[str, Any]:
        return await fetch_specs_detailed(
            part_number,
            credentials,
            jlcparts_db_path=jlcparts_path,
            search_config=search_config,
        )

    return fetcher


def _interactive_approval(tool_name: str, arguments: dict[str, Any]) -> bool:
    """Require a human response on the controlling terminal for mutations."""
    try:
        tty = open("/dev/tty", "r+")
    except OSError:
        return False
    try:
        target = arguments.get("part_id", arguments.get("part_ids", "selection"))
        fields = arguments.get("fields")
        effect = f"target {target}"
        if isinstance(fields, dict):
            effect += f", fields {sorted(fields)}"
        print(f"Approve Parts Bin {tool_name} ({effect})? [y/N] ", file=tty, flush=True)
        return tty.readline().strip().lower() in {"y", "yes"}
    finally:
        tty.close()


if __name__ == "__main__":
    main()
