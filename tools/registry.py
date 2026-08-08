"""The single typed, transport-neutral Parts Bin tool registry.

This module deliberately knows about the domain service, but not FastAPI, MCP,
LLM clients, prompts, or persistence.  Its JSON schemas are the contract that
other transports consume.
"""

from __future__ import annotations

import inspect
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from domain import (
    AddPartRequest, AddStockRequest, ApplyReviewRequest, BulkUpdateRequest,
    DeletePartRequest, DomainError, FetchSpecsRequest, GetPartRequest,
    PartFields, PartsBinService, ProvenanceRequest, RejectReviewRequest,
    SearchPartsRequest, UpdatePartRequest, ErrorCode,
)

ToolResult = dict[str, Any]
ApprovalChecker = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]

_FIELDS = {
    "part_category": {"type": "string", "minLength": 1},
    "profile": {"type": "string", "enum": ["passive", "discrete_ic"]},
    "quantity": {"type": "integer", "minimum": 0},
    "value": {"type": ["string", "null"]},
    "package": {"type": ["string", "null"]},
    "part_number": {"type": ["string", "null"]},
    "manufacturer": {"type": ["string", "null"]},
    "description": {"type": ["string", "null"]},
}
_EDITABLE_FIELDS = {name: schema for name, schema in _FIELDS.items()}
_PART_PROPERTIES = {"id": {"type": "integer"}, **{k: v for k, v in _FIELDS.items()}}
_PART_SCHEMA = {"type": "object", "additionalProperties": False, "properties": _PART_PROPERTIES}


def _fields_schema(*, required: list[str] | None = None, min_properties: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object", "additionalProperties": False, "properties": _EDITABLE_FIELDS,
    }
    if required:
        schema["required"] = required
    if min_properties is not None:
        schema["minProperties"] = min_properties
    return schema


def _tool(name: str, description: str, properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": properties},
        "outputSchema": {"type": "object"},
    }
    if required:
        result["inputSchema"]["required"] = required
    return result


@dataclass(frozen=True)
class ApprovalReceipt:
    """A receipt issued by the host approval engine, not by a tool caller."""

    tool_name: str
    arguments_fingerprint: str

    @classmethod
    def issue(cls, tool_name: str, arguments: Mapping[str, Any]) -> "ApprovalReceipt":
        return cls(tool_name, _fingerprint(arguments))


@dataclass(frozen=True)
class ToolExecutionContext:
    approval: ApprovalReceipt | None = None


class PartsBinToolRegistry:
    """Canonical definitions and execution mapping for Parts Bin tools."""

    def __init__(self, service: PartsBinService, *, approval_checker: ApprovalChecker | None = None):
        self.service = service
        self.approval_checker = approval_checker
        self._tools = {tool["name"]: tool for tool in _TOOL_DEFINITIONS}

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.copy() for tool in _TOOL_DEFINITIONS]

    def list_resources(self) -> list[dict[str, str]]:
        return [
            {"uri": "parts-bin://field-definitions", "name": "Parts Bin field definitions", "mimeType": "application/json"},
            {"uri": "parts-bin://normalization-rules", "name": "Parts Bin normalization rules", "mimeType": "application/json"},
        ]

    def read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "parts-bin://field-definitions":
            return {"fields": _FIELDS, "editable_fields": sorted(_EDITABLE_FIELDS)}
        if uri == "parts-bin://normalization-rules":
            return {"value": "passive values are normalized by category; EIA notation is expanded", "examples": {"10K": "10k", "2R2": "2.2r", "100nF": "100n"}}
        raise DomainError(ErrorCode.INVALID_INPUT, "Unknown resource", details={"uri": uri})

    async def execute(self, name: str, arguments: Mapping[str, Any] | None = None, *, context: ToolExecutionContext | None = None) -> ToolResult:
        args = dict(arguments or {})
        if name not in self._tools:
            return _error("invalid_input", "Unknown tool", {"tool": name})
        try:
            _validate(name, args, self._tools[name]["inputSchema"])
            if name == "add_part":
                _validate_add_part_completeness(args)
            if name in _APPROVAL_REQUIRED and not await self._approved(name, args, context):
                return _error(str(ErrorCode.APPROVAL_REQUIRED), "Explicit user approval is required for this mutation", {"tool": name})
            result = await self._dispatch(name, args)
            return {"ok": True, "result": result}
        except DomainError as exc:
            return _error(str(exc.code), exc.message, exc.details)
        except (TypeError, ValueError) as exc:
            return _error("invalid_input", str(exc), {})

    async def _approved(self, name: str, args: dict[str, Any], context: ToolExecutionContext | None) -> bool:
        if context is None or context.approval is None:
            return False
        if context.approval.tool_name != name:
            return False
        if context.approval.arguments_fingerprint != _fingerprint(args):
            return False
        if self.approval_checker is None:
            return False
        decision = self.approval_checker(name, args)
        return await decision if inspect.isawaitable(decision) else bool(decision)

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name == "search_parts":
            limit = args.get("limit", 20)
            rows = self.service.search(SearchPartsRequest(args.get("filters", {})))
            return {"parts": [_compact_part(row) for row in rows[:limit]], "count": len(rows), "truncated": len(rows) > limit}
        if name == "get_part":
            return _compact_part(self.service.get(GetPartRequest(args["part_id"])))
        if name == "add_part":
            fields = {key: args.get(key) for key in _FIELDS}
            return _compact_part(self.service.add_part(AddPartRequest(PartFields(**fields))))
        if name == "add_stock":
            return _compact_part(self.service.add_stock(AddStockRequest(args["part_id"], args["quantity"])))
        if name == "update_part":
            return _compact_part(self.service.update_part(UpdatePartRequest(args["part_id"], args["fields"])))
        if name == "bulk_update_parts":
            rows = self.service.bulk_update(BulkUpdateRequest(tuple(args["part_ids"]), args["fields"]))
            return {"parts": [_compact_part(row) for row in rows]}
        if name == "delete_part":
            self.service.delete_part(DeletePartRequest(args["part_id"]))
            return {"part_id": args["part_id"], "deleted": True}
        if name == "lookup_part_specs":
            result = await self.service.fetch_and_stage_specs(FetchSpecsRequest(args["part_id"]))
            return {key: (_compact_part(value) if key == "part" else value) for key, value in result.items() if key in {"part", "chosen_updates", "provider", "outcome", "status", "tried_providers"}}
        if name == "list_pending_reviews":
            reviews = self.service.list_pending_reviews()
            return {"reviews": [{"part_id": part_id, **review} for part_id, review in reviews.items()]}
        if name == "apply_review":
            part = self.service.apply_review(ApplyReviewRequest(args["part_id"], args.get("updates")))
            return _compact_part(part)
        if name == "reject_review":
            self.service.reject_review(RejectReviewRequest(args["part_id"], tuple(args["fields"]) if "fields" in args else None))
            return {"part_id": args["part_id"], "rejected": True}
        if name == "get_provenance":
            return {"part_id": args["part_id"], "provenance": self.service.provenance(ProvenanceRequest(args["part_id"]))}
        raise AssertionError(name)


def _compact_part(part: Any) -> dict[str, Any]:
    return {"id": part.id, "part_category": part.part_category, "profile": part.profile, "value": part.value,
            "package": part.package, "part_number": part.part_number, "quantity": part.quantity,
            "manufacturer": part.manufacturer, "description": part.description}


def _error(code: str, message: str, details: dict[str, Any]) -> ToolResult:
    return {"ok": False, "error": {"code": code, "message": message, "details": details}}


def _fingerprint(arguments: Mapping[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _validate(name: str, args: dict[str, Any], schema: dict[str, Any]) -> None:
    # Deliberately small dependency-free JSON-schema subset. Domain validation
    # remains authoritative after this shape/type gate.
    if not isinstance(args, dict) or any(key not in schema.get("properties", {}) for key in args):
        raise DomainError(ErrorCode.INVALID_INPUT, f"Invalid arguments for {name}", details={"tool": name})
    missing = [key for key in schema.get("required", []) if key not in args]
    if missing:
        raise DomainError(ErrorCode.INVALID_INPUT, "Required argument is missing", details={"fields": missing})
    for key, value in args.items():
        rule = schema["properties"][key]
        if not _matches(value, rule):
            raise DomainError(ErrorCode.INVALID_INPUT, f"Invalid argument: {key}", details={"field": key})
    for key, rule in schema.get("properties", {}).items():
        if key in args and isinstance(args[key], dict) and rule.get("additionalProperties") is False:
            _validate_object(args[key], rule)


def _validate_add_part_completeness(args: dict[str, Any]) -> None:
    required = "value" if args.get("profile") == "passive" else "part_number"
    if not isinstance(args.get(required), str) or not args[required].strip():
        raise DomainError(ErrorCode.INVALID_INPUT, f"{required} is required for {args['profile']} parts", details={"field": required})


def _validate_object(value: dict[str, Any], schema: dict[str, Any]) -> None:
    if any(key not in schema.get("properties", {}) for key in value):
        raise DomainError(ErrorCode.INVALID_INPUT, "Unknown field", details={})
    if len(value) < schema.get("minProperties", 0):
        raise DomainError(ErrorCode.INVALID_INPUT, "At least one field is required", details={})
    for key, item in value.items():
        if not _matches(item, schema["properties"][key]):
            raise DomainError(ErrorCode.INVALID_INPUT, f"Invalid field: {key}", details={"field": key})


def _matches(value: Any, rule: dict[str, Any]) -> bool:
    types = rule.get("type", [])
    if isinstance(types, str):
        types = [types]
    valid_type = any((kind == "string" and isinstance(value, str)) or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool)) or (kind == "object" and isinstance(value, dict)) or (kind == "array" and isinstance(value, list)) or (kind == "null" and value is None) for kind in types)
    if not valid_type or "enum" in rule and value not in rule["enum"]:
        return False
    if isinstance(value, str) and len(value) < rule.get("minLength", 0):
        return False
    if isinstance(value, int) and (value < rule.get("minimum", value) or value > rule.get("maximum", value)):
        return False
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0) or len(value) > rule.get("maxItems", len(value)):
            return False
        if "items" in rule and not all(_matches(item, rule["items"]) for item in value):
            return False
    return True


_TOOL_DEFINITIONS = [
    _tool("search_parts", "Search committed inventory using exact typed filters.", {"filters": {"type": "object", "additionalProperties": False, "properties": {key: value for key, value in _FIELDS.items() if key in {"part_category", "profile", "value", "package", "part_number"}}}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},),
    _tool("get_part", "Get one committed part by id.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("add_part", "Add one distinct part.", _FIELDS, required=["part_category", "profile", "quantity"]),
    _tool("add_stock", "Add positive stock to one part.", {"part_id": {"type": "integer", "minimum": 1}, "quantity": {"type": "integer", "minimum": 1}}, required=["part_id", "quantity"]),
    _tool("update_part", "Update explicit fields on one part.", {"part_id": {"type": "integer", "minimum": 1}, "fields": _fields_schema(min_properties=1)}, required=["part_id", "fields"]),
    _tool("bulk_update_parts", "Update explicit fields on an explicit part selection.", {"part_ids": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "integer", "minimum": 1}}, "fields": _fields_schema(min_properties=1)}, required=["part_ids", "fields"]),
    _tool("delete_part", "Delete one identified part.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("lookup_part_specs", "Fetch and stage supplier specifications for review.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("list_pending_reviews", "List staged enrichment reviews.", {}),
    _tool("apply_review", "Apply a pending review for one part.", {"part_id": {"type": "integer", "minimum": 1}, "updates": _fields_schema()}, required=["part_id"]),
    _tool("reject_review", "Reject a pending review, wholly or by field.", {"part_id": {"type": "integer", "minimum": 1}, "fields": {"type": "array", "items": {"type": "string", "minLength": 1}}}, required=["part_id"]),
    _tool("get_provenance", "Get accepted field provenance for one part.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
]

_APPROVAL_REQUIRED = {"update_part", "bulk_update_parts", "delete_part", "apply_review", "reject_review"}
