import json

import pytest

from domain import AddPartRequest, PartFields, PartsBinService
from tools import ApprovalReceipt, PartsBinToolRegistry, ToolExecutionContext
from tools.mcp_server import MCPServer, serve_stdio


def _fields(**overrides):
    values = {"part_category": "resistor", "profile": "passive", "quantity": 2, "value": "10K", "package": "0402"}
    values.update(overrides)
    return PartFields(**values)


@pytest.fixture
def registry(tmp_path):
    return PartsBinToolRegistry(PartsBinService(tmp_path / "parts.db"), approval_checker=lambda _name, _args: True)


@pytest.mark.asyncio
async def test_registry_is_schema_first_and_search_is_compact(registry):
    names = [tool["name"] for tool in registry.list_tools()]
    assert names == ["search_parts", "get_part", "add_part", "add_stock", "update_part", "bulk_update_parts", "delete_part", "lookup_part_specs", "list_pending_reviews", "apply_review", "reject_review", "get_provenance"]
    added = await registry.execute("add_part", {**vars(_fields()), "quantity": 2})
    assert added["ok"] is True
    found = await registry.execute("search_parts", {"filters": {"part_category": "resistor"}})
    assert found["result"]["parts"] == [{"id": 1, "part_category": "resistor", "profile": "passive", "value": "10k", "package": "0402", "part_number": None, "quantity": 2, "manufacturer": None, "description": None}]
    assert "created_at" not in found["result"]["parts"][0]


@pytest.mark.asyncio
async def test_registry_rejects_unknown_fields_and_requires_server_approval(registry):
    invalid = await registry.execute("get_part", {"part_id": 1, "sql": "select 1"})
    assert invalid == {"ok": False, "error": {"code": "invalid_input", "message": "Invalid arguments for get_part", "details": {"tool": "get_part"}}}
    part = registry.service.add_part(AddPartRequest(_fields()))
    denied = await PartsBinToolRegistry(registry.service).execute("delete_part", {"part_id": part.id})
    assert denied["error"]["code"] == "approval_required"
    approved = await registry.execute("delete_part", {"part_id": part.id}, context=ToolExecutionContext(ApprovalReceipt.issue("delete_part", {"part_id": part.id})))
    assert approved["result"]["deleted"] is True


@pytest.mark.asyncio
async def test_mcp_projection_matches_registry_and_returns_json(registry):
    server = MCPServer(registry)
    listed = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed["result"]["tools"] == registry.list_tools()
    response = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_pending_reviews", "arguments": {}}})
    assert response["result"]["isError"] is False
    json.loads(response["result"]["content"][0]["text"])
    resources = await server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert [item["uri"] for item in resources["result"]["resources"]] == ["parts-bin://field-definitions", "parts-bin://normalization-rules"]


@pytest.mark.asyncio
async def test_mcp_initialize_negotiates_requested_protocol_version(registry):
    response = await MCPServer(registry).handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert response["result"]["protocolVersion"] == "2025-03-26"

    unsupported = await MCPServer(registry).handle({
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    })
    assert unsupported["result"]["protocolVersion"] == "2025-06-18"


@pytest.mark.asyncio
async def test_standalone_stdio_client_completes_every_inventory_workflow(tmp_path, capsys):
    async def fetcher(_part_number):
        return {
            "chosen_updates": {"manufacturer": "Acme"},
            "durable_provenance": [{
                "field_name": "manufacturer", "field_value": "Acme",
                "source_tier": "fixture", "source_kind": "test",
                "extraction_method": "fixture",
            }],
            "provider": "fixture",
            "outcome": "match",
        }

    service = PartsBinService(tmp_path / "parts.db", spec_fetcher=fetcher)
    registry = PartsBinToolRegistry(service, approval_checker=lambda _name, _args: True)
    server = MCPServer(registry)
    requests = []

    def call(name, arguments):
        request_id = len(requests) + 1
        requests.append({
            "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })

    call("add_part", {"part_category": "resistor", "profile": "passive", "quantity": 2, "value": "10K", "package": "0402"})
    call("get_part", {"part_id": 1})
    call("add_stock", {"part_id": 1, "quantity": 1})
    call("search_parts", {"filters": {"part_category": "resistor"}})
    call("update_part", {"part_id": 1, "fields": {"description": "updated"}})
    call("bulk_update_parts", {"part_ids": [1], "fields": {"package": "0603"}})
    call("get_provenance", {"part_id": 1})
    call("add_part", {"part_category": "transistor", "profile": "discrete_ic", "quantity": 1, "part_number": "2N7002"})
    call("lookup_part_specs", {"part_id": 2})
    call("list_pending_reviews", {})
    call("apply_review", {"part_id": 2})
    call("lookup_part_specs", {"part_id": 2})
    call("reject_review", {"part_id": 2, "fields": ["manufacturer"]})
    call("delete_part", {"part_id": 1})

    await serve_stdio(server, [json.dumps(request) for request in requests])
    responses = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(responses) == len(requests)
    failures = [response for response in responses if response.get("result", {}).get("isError") is not False]
    assert not failures, failures
    assert [tool["name"] for tool in registry.list_tools()] == [
        "search_parts", "get_part", "add_part", "add_stock", "update_part",
        "bulk_update_parts", "delete_part", "lookup_part_specs",
        "list_pending_reviews", "apply_review", "reject_review", "get_provenance",
    ]


@pytest.mark.asyncio
async def test_mcp_approval_is_server_supplied_not_client_controlled(registry):
    server = MCPServer(registry)
    part = registry.service.add_part(AddPartRequest(_fields()))
    rejected = await server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "delete_part", "arguments": {"part_id": part.id, "approval": True}},
    })
    assert rejected["result"]["isError"] is True
    approved = await server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "delete_part", "arguments": {"part_id": part.id}},
    })
    assert approved["result"]["isError"] is False
    assert approved["result"]["structuredContent"]["result"]["deleted"] is True
