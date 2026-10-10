"""The single typed, transport-neutral Parts Bin tool registry.

This module deliberately knows about the domain service, but not FastAPI,
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
    SearchPartsRequest, SearchCandidatesRequest, UpdatePartRequest, ErrorCode,
)
from domain.repositories import StoredOperation
from domain.specifications import contract

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
    operation_id: str | None = None
    execution_id: str | None = None
    worker_id: str | None = None


class PartsBinToolRegistry:
    """Canonical definitions and execution mapping for Parts Bin tools."""

    def __init__(self, service: PartsBinService, *, approval_checker: ApprovalChecker | None = None):
        self.service = service
        self.approval_checker = approval_checker
        self._tools = {tool["name"]: tool for tool in _TOOL_DEFINITIONS}

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.copy() for tool in _TOOL_DEFINITIONS]

    @staticmethod
    def approval_targets(name: str, arguments: dict[str, Any]) -> tuple[int, ...]:
        if name not in _APPROVAL_REQUIRED:
            raise ValueError("Tool does not require approval")
        return tuple(arguments["part_ids"]) if name == "bulk_update_parts" else (arguments["part_id"],)

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
            if name in {"add_part", "add_stock"} and context is not None and context.operation_id:
                result = await self._execute_addition(name, args, context)
            else:
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

    async def _execute_addition(self, name: str, args: dict[str, Any], context: ToolExecutionContext) -> Any:
        with self.service.transaction() as (service, repository):
            if context.execution_id and context.worker_id:
                repository.executions.assert_owned(context.execution_id, context.worker_id)
            previous = repository.operations.get(context.operation_id)
            if previous is not None:
                if previous.tool_name != name or previous.arguments != args:
                    raise DomainError(ErrorCode.CONFLICT, "Operation identity was reused with different arguments")
                return previous.result
            bound = PartsBinToolRegistry(service)
            result = await bound._dispatch(name, args, enrich=False)
            if name == "add_part" and service.should_enrich(result):
                # A crash after commit must not automatically repeat paid retrieval.
                result["enrichment"] = {"status": "interrupted", "retry_tool": "lookup_part_specs"}
            repository.operations.insert(StoredOperation(context.operation_id, name, args, result))
        if name == "add_part" and self.service.should_enrich(result):
            result = await self._enrich_added(result)
            with self.service.repository.transaction() as repository:
                repository.operations.save_result(context.operation_id, result)
        return result

    async def _enrich_added(self, result: dict[str, Any]) -> dict[str, Any]:
        try:
            lookup = await self.service.fetch_and_stage_specs(FetchSpecsRequest(result["id"]))
            result["enrichment"] = {key: value for key, value in lookup.items()
                                    if key in {"chosen_updates", "provider", "outcome", "status", "tried_providers"}}
        except DomainError as exc:
            result["enrichment"] = {"status": "unavailable", "code": str(exc.code)}
        except Exception:
            result["enrichment"] = {"status": "failed"}
        return result

    async def _dispatch(self, name: str, args: dict[str, Any], *, enrich: bool = True) -> Any:
        if name == "list_categories":
            return {"categories": [vars(category) for category in self.service.list_categories()]}
        if name == "search_parts":
            limit = args.get("limit", 20)
            request = SearchPartsRequest(args.get("filters", {}), args.get("minimum_quantity", 0))
            offset = args.get("offset", 0)
            if 'requirements' in args:
                return self.service.search_specifications(request, args['requirements'], limit=limit, offset=offset)
            page = self.service.search_page(request, limit=limit, offset=offset)
            return {**page, "parts": [_compact_part(row) for row in page["parts"]]}
        if name == "search_candidates":
            page = self.service.candidate_page(SearchCandidatesRequest(
                args["query"], args.get("filters", {}), args.get("minimum_quantity", 0)),
                limit=args.get("limit", 20), offset=args.get("offset", 0))
            return {**page, "candidates": [_compact_part(row) for row in page["candidates"]]}
        if name == 'get_specification_contract':
            return contract(args['category'])
        if name == 'get_specifications':
            return self.service.get_specifications(args['part_id'])
        if name == 'stage_specification_review':
            # Models may record user assertions, but cannot promote their own
            # invented source metadata to independently sourced evidence.
            if any(fact['evidence'].get('kind') != 'user_assertion' for fact in args['facts']):
                raise DomainError(ErrorCode.INVALID_INPUT, 'Source facts must come through reviewed source ingestion')
            self.service.stage_specifications(self.service.get(GetPartRequest(args['part_id'])), args['facts'])
            return self.service.get_specifications(args['part_id'])
        if name == 'apply_specification_review':
            return self.service.apply_specification_review(args['part_id'])
        if name == 'reject_specification_review':
            self.service.reject_specification_review(args['part_id'])
            return {'part_id': args['part_id'], 'rejected': True}
        if name == "get_part":
            return _compact_part(self.service.get(GetPartRequest(args["part_id"])))
        if name == "add_part":
            fields = {key: args.get(key) for key in _FIELDS}
            part = self.service.add_part(AddPartRequest(PartFields(**fields)))
            result = _compact_part(part)
            if enrich and self.service.should_enrich(result):
                result = await self._enrich_added(result)
            return result
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
            return self.service.pending_review_page(**args)
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
    valid_type = any((kind == "string" and isinstance(value, str)) or (kind == "boolean" and isinstance(value, bool)) or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool)) or (kind == "object" and isinstance(value, dict)) or (kind == "array" and isinstance(value, list)) or (kind == "null" and value is None) for kind in types)
    if not valid_type or "enum" in rule and value not in rule["enum"]:
        return False
    if isinstance(value, dict) and 'properties' in rule:
        if rule.get('additionalProperties') is False and set(value) - set(rule['properties']):
            return False
        if any(key not in value for key in rule.get('required', [])):
            return False
        if any(not _matches(item, rule['properties'][key]) for key, item in value.items() if key in rule['properties']):
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


_CONDITIONS_SCHEMA = {"type": "object"}  # Domain validates bounded condition names and values.
_SPEC_PROPERTIES = {
    'name': {'type': 'string', 'minLength': 1},
    'value': {'type': 'string', 'minLength': 1},
    'basis': {'type': 'string', 'minLength': 1},
    'conditions': _CONDITIONS_SCHEMA,
}
_REQUIREMENTS_SCHEMA = {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {
    'type': 'object', 'additionalProperties': False,
    'properties': {**_SPEC_PROPERTIES, 'comparison': {'type': 'string', 'enum': ['eq', 'gte', 'lte']}},
    'required': [*_SPEC_PROPERTIES, 'comparison'],
}}
_FACTS_SCHEMA = {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {
    'type': 'object', 'additionalProperties': False,
    'properties': {**_SPEC_PROPERTIES, 'evidence': {
        'type': 'object', 'additionalProperties': False,
        'properties': {'kind': {'type': 'string', 'enum': ['user_assertion']},
                       'excerpt': {'type': 'string', 'minLength': 1}},
        'required': ['kind', 'excerpt'],
    }}, 'required': [*_SPEC_PROPERTIES, 'evidence'],
}}

_TOOL_DEFINITIONS = [
    _tool("list_categories", "Discover exact category names in committed inventory before choosing category filters for search_parts. Returns part_category, part_count (distinct stock records), and total_quantity (sum of stored quantities). Includes categories whose stock is zero. Pending reviews are excluded. These counts do not establish electrical suitability; search_parts retrieves the relevant stock.", {}),
    _tool("search_parts", "Search committed inventory. Pages are ordered by part id. Start with offset 0; when next_offset is non-null, reuse the same filters, requirements, minimum_quantity, and limit with that offset. Counts cover all results. For specification searches, limit and offset apply separately to matches and incomplete candidates (at most 2 * limit records per page). next_offset is null when both groups are exhausted. If inventory or accepted facts change, restart pagination. Op amp, opamp, op-amp, and operational amplifier categories match as synonyms without rewriting stock. Passive values compare equivalent nominal units (10 kΩ = 10000r, 0.1 µF = 100nF). Package and full part number match exactly; omit an uncertain package and ask for clarification. minimum_quantity is available stock per record. Discover supported specification requirements with get_specification_contract before querying requirements. Returns confirmed matches with supporting source evidence separately from incomplete candidates. All qualifiers and conditions must match; ratings do not establish application suitability.", {"filters": {"type": "object", "additionalProperties": False, "properties": {key: value for key, value in _FIELDS.items() if key in {"part_category", "profile", "value", "package", "part_number"}}}, "requirements": _REQUIREMENTS_SCHEMA, "minimum_quantity": {"type": "integer", "minimum": 0}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},),
    _tool("search_candidates", "Find candidate stock by a literal, case-insensitive substring across committed descriptions, manufacturers, and part numbers. Markings can be found only if recorded in those fields. query is one contiguous fragment, not a natural-language question or wildcard. Optional filters use search_parts exact identity and nominal-value rules; minimum_quantity applies per record. Results are candidates, not verified identities, interchangeable stock, or evidence of electrical suitability. Preserve every returned ordering suffix and quantity; clarify ambiguous markings before adding or merging stock. Pending proposals are excluded. Results are ordered by id and bounded by limit. Start with offset 0 and follow next_offset with the same query, filters, minimum_quantity, and limit until null. Counts cover all candidates. Restart if inventory changes.", {
        "query": {"type": "string", "minLength": 1},
        "filters": {"type": "object", "additionalProperties": False, "properties": {key: value for key, value in _FIELDS.items() if key in {"part_category", "profile", "value", "package", "part_number"}}},
        "minimum_quantity": {"type": "integer", "minimum": 0},
        "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }, required=["query"]),
    _tool("get_part", "Get one committed part by id.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("add_part", "Add one distinct part.", _FIELDS, required=["part_category", "profile", "quantity"]),
    _tool("add_stock", "Add positive stock to one part.", {"part_id": {"type": "integer", "minimum": 1}, "quantity": {"type": "integer", "minimum": 1}}, required=["part_id", "quantity"]),
    _tool("update_part", "Update explicit fields on one part.", {"part_id": {"type": "integer", "minimum": 1}, "fields": _fields_schema(min_properties=1)}, required=["part_id", "fields"]),
    _tool("bulk_update_parts", "Update explicit fields on an explicit part selection.", {"part_ids": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "integer", "minimum": 1}}, "fields": _fields_schema(min_properties=1)}, required=["part_ids", "fields"]),
    _tool("delete_part", "Delete one identified part.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("lookup_part_specs", "Fetch and stage supplier specifications for review.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("list_pending_reviews", "List pending base-metadata proposals, not committed fields or electrical-rating reviews. Each fields entry contains the proposed value and an accepted flag used to select review fields; accepted does not mean the proposal has been applied. Use get_part for committed identity and quantity, and get_specifications for pending electrical facts. Filter by value (equivalent nominal units), exact part_number, or part_id; filters match proposed or committed fields. Pages are ordered by part_id; follow next_offset with unchanged arguments until null. Discovery omits provenance; include_provenance requires a single part_id.", {"part_id": {"type": "integer", "minimum": 1}, "value": {"type": "string", "minLength": 1}, "part_number": {"type": "string", "minLength": 1}, "include_provenance": {"type": "boolean"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _tool("apply_review", "Apply a pending review for one part.", {"part_id": {"type": "integer", "minimum": 1}, "updates": _fields_schema()}, required=["part_id"]),
    _tool("reject_review", "Reject a pending review, wholly or by field.", {"part_id": {"type": "integer", "minimum": 1}, "fields": {"type": "array", "items": {"type": "string", "minLength": 1}}}, required=["part_id"]),
    _tool("get_provenance", "Get accepted field provenance for one part.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool('get_specification_contract', 'Discover supported fields, units, qualifiers, and comparisons for any inventory category. Unsupported categories remain valid inventory.', {'category': {'type': 'string', 'minLength': 1}}, required=['category']),
    _tool('get_specifications', 'Read accepted electrical facts and pending specification review for one exact part. Pending facts are not confirmed.', {'part_id': {'type': 'integer', 'minimum': 1}}, required=['part_id']),
    _tool('stage_specification_review', 'Stage explicitly user-asserted electrical facts for review. Quote the user assertion; do not invent source evidence. Assertions do not confirm source-backed search requirements.', {'part_id': {'type': 'integer', 'minimum': 1}, 'facts': _FACTS_SCHEMA}, required=['part_id', 'facts']),
    _tool('apply_specification_review', 'Accept the pending electrical facts for this exact part, preserving source passages, conditions, and evidence kind. Approval does not turn user assertions into source evidence.', {'part_id': {'type': 'integer', 'minimum': 1}}, required=['part_id']),
    _tool('reject_specification_review', 'Discard a pending electrical specification review without changing accepted facts.', {'part_id': {'type': 'integer', 'minimum': 1}}, required=['part_id']),
]

_APPROVAL_REQUIRED = {"update_part", "bulk_update_parts", "delete_part", "apply_review", "reject_review", "apply_specification_review", "reject_specification_review"}
