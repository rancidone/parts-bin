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
    CreatePartsRequest, UpdatePartsRequest, AddStockBatchRequest,
    AddPartRequest, AddStockRequest, ApplyReviewRequest, BulkUpdateRequest,
    DeletePartRequest, DomainError, FetchSpecsRequest, GetPartRequest,
    PartFields, PartsBinService, ProvenanceRequest, RejectReviewRequest,
    SearchPartsRequest, SearchCandidatesRequest, UpdatePartRequest, IngestDatasheetRequest, ErrorCode,
    CheckInventoryRequest, InventoryCheckItem,
)
from domain.repositories import StoredOperation
from domain.specifications import contract
from domain.nominal import BASE_UNITS

ToolResult = dict[str, Any]
ApprovalChecker = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]

_FIELDS = {
    "part_category": {"type": "string", "minLength": 1},
    "profile": {"type": "string", "enum": ["passive", "discrete_ic"]},
    "quantity": {"type": "integer", "minimum": 0},
    "value": {"type": ["string", "null"]},
    "value_unit": {"type": ["string", "null"], "enum": [None, *BASE_UNITS], "description": "Base unit of a decimal nominal value: Ω, F, H, A, V, W, Hz, s, m, cd, VA, %, ratio or V/s. Explicit units in value are also accepted. Known passive categories imply their unit."},
    "package": {"type": ["string", "null"]},
    "part_number": {"type": ["string", "null"]},
    "manufacturer": {"type": ["string", "null"]},
    "description": {"type": ["string", "null"]},
    "datasheet_url": {"type": ["string", "null"], "maxLength": 2048},
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
        if name == "bulk_update_parts":
            return tuple(arguments["part_ids"])
        if name == "update_parts":
            return tuple(item["part_id"] for item in arguments["items"])
        return (arguments["part_id"],)

    async def execute(self, name: str, arguments: Mapping[str, Any] | None = None, *, context: ToolExecutionContext | None = None) -> ToolResult:
        args = dict(arguments or {})
        if name not in self._tools:
            return _error("invalid_input", "Unknown tool", {"tool": name})
        try:
            if name == "search_parts" and args.get("expression") is not None:
                self.service._search_expression(args["expression"])
            _validate(name, args, self._tools[name]["inputSchema"])
            if name == "add_part":
                _validate_add_part_completeness(args)
            if name == "add_parts":
                for item in args["items"]:
                    _validate_add_part_completeness(item)
            if name == "update_parts":
                self.service._validate_batch(args["items"], part_ids=tuple(item["part_id"] for item in args["items"]))
                if any(not item["fields"] for item in args["items"]):
                    raise DomainError(ErrorCode.INVALID_INPUT, "Each update needs fields")
            if name in _APPROVAL_REQUIRED and not await self._approved(name, args, context):
                return _error(str(ErrorCode.APPROVAL_REQUIRED), "Explicit user approval is required for this mutation", {"tool": name})
            if name in {"add_part", "add_stock", "add_parts", "add_stock_batch"} and context is not None and context.operation_id:
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
            if name == "add_part" and service.should_enrich(result, enabled=args.get("enrich", True)):
                # A crash after commit must not automatically repeat paid retrieval.
                result["enrichment"] = {"status": "interrupted", "retry_tool": "lookup_part_specs"}
            repository.operations.insert(StoredOperation(context.operation_id, name, args, result))
        if name == "add_part" and self.service.should_enrich(result, enabled=args.get("enrich", True)):
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
            request = SearchPartsRequest(args.get("filters", {}), args.get("minimum_quantity", 0), args.get("expression"))
            offset = args.get("offset", 0)
            if 'requirements' in args:
                return self.service.search_specifications(request, args['requirements'], limit=limit, offset=offset)
            page = self.service.search_page(request, limit=limit, offset=offset)
            return {**page, "parts": [_compact_part(row) for row in page["parts"]],
                    "partial_matches": [{**item, "part": _compact_part(item["part"])} for item in page["partial_matches"]]}
        if name == "search_candidates":
            page = self.service.candidate_page(SearchCandidatesRequest(
                args["query"], args.get("filters", {}), args.get("minimum_quantity", 0)),
                limit=args.get("limit", 20), offset=args.get("offset", 0))
            return {**page, "candidates": [_compact_part(row) for row in page["candidates"]]}
        if name == "check_inventory":
            result = self.service.check_inventory(CheckInventoryRequest(tuple(
                InventoryCheckItem(item["filters"], item.get("quantity")) for item in args["items"])))
            fields = ("id", "part_number", "part_category", "value", "value_unit", "display_value", "package", "quantity")
            return {**result, "items": [
                {**item, "parts": [{key: getattr(part, key) for key in fields} for part in item["parts"]]}
                for item in result["items"]]}
        if name == 'get_specification_contract':
            return contract(args['category'])
        if name == 'get_specifications':
            return self.service.get_specifications(args['part_id'])
        if name == 'ingest_datasheet':
            return await self.service.ingest_datasheet(IngestDatasheetRequest(args['part_id'], args['source_url']))
        if name == 'list_pending_specification_reviews':
            return self.service.specification_review_page(**args)
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
        if name == "add_parts":
            outcomes = self.service.create_parts(CreatePartsRequest(tuple(PartFields.from_mapping(item) for item in args["items"])))
            added = [_compact_part(item.part) for item in outcomes if item.status == 'added']
            return {"parts": added, "count": len(added), "skipped_count": len(outcomes) - len(added),
                    "items": [{"index": item.index, "status": item.status, "reason": item.reason,
                               "duplicate_of": item.duplicate_of, "part": _compact_part(item.part)}
                              for item in outcomes]}
        if name == "add_stock_batch":
            parts = self.service.add_stock_batch(AddStockBatchRequest(tuple(AddStockRequest(**item) for item in args["items"])))
            return {"parts": [_compact_part(part) for part in parts], "count": len(parts)}
        if name == "update_parts":
            parts = self.service.update_parts(UpdatePartsRequest(tuple(UpdatePartRequest(**item) for item in args["items"])))
            return {"parts": [_compact_part(part) for part in parts], "count": len(parts)}
        if name == "add_part":
            fields = {key: args.get(key) for key in _FIELDS}
            part = self.service.add_part(AddPartRequest(PartFields(**fields)))
            result = _compact_part(part)
            if args.get("enrich") is False:
                result["enrichment"] = {"status": "skipped"}
            elif enrich and self.service.should_enrich(result):
                result = await self._enrich_added(result)
            return result
        if name == "add_stock":
            return _compact_part(self.service.add_stock(AddStockRequest(args["part_id"], args["quantity"])))
        if name == "update_part":
            return vars(self.service.update_part(UpdatePartRequest(args["part_id"], args["fields"])))
        if name == "bulk_update_parts":
            result = self.service.bulk_update(BulkUpdateRequest(tuple(args["part_ids"]), args["fields"]))
            return {**result, "parts": [_compact_part(row) for row in result["parts"]]}
        if name == "delete_part":
            self.service.delete_part(DeletePartRequest(args["part_id"]))
            return {"part_id": args["part_id"], "deleted": True}
        if name == "lookup_part_specs":
            result = await self.service.fetch_and_stage_specs(FetchSpecsRequest(args["part_id"]))
            return {key: (_compact_part(value) if key == "part" else value) for key, value in result.items() if key in {"part", "chosen_updates", "provider", "outcome", "status", "tried_providers", "lookup_candidates", "candidate_count"}}
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
    return {"id": part.id, "part_category": part.part_category, "profile": part.profile, "value": part.value, "value_unit": part.value_unit, "display_value": part.display_value,
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
            bounds = {bound: rule[bound] for bound in ("minimum", "maximum") if bound in rule}
            hint = f" (expected {bounds['minimum']} through {bounds['maximum']})" if set(bounds) == {"minimum", "maximum"} else ""
            raise DomainError(ErrorCode.INVALID_INPUT, f"Invalid argument: {key}{hint}",
                              details={"field": key, **bounds})
    for key, rule in schema.get("properties", {}).items():
        if key in args and isinstance(args[key], dict) and rule.get("additionalProperties") is False:
            _validate_object(args[key], rule)


def _validate_add_part_completeness(args: dict[str, Any]) -> None:
    if (args.get('profile') == 'discrete_ic' and not args.get('part_number')
            and isinstance(args.get('description'), str) and args['description'].strip()):
        # A user-described physical group can be stored without inventing an
        # ordering code. Its description does not confer verified identity.
        return
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
    if rule.get("$ref") == "#/$defs/search_expression":
        rule = _EXPRESSION_SCHEMA
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

_SEARCH_FILTER_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    key: value for key, value in _FIELDS.items() if key in {"part_category", "profile", "value", "value_unit", "package", "part_number"}}}
_EXPRESSION_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "filters": _SEARCH_FILTER_SCHEMA,
    "all": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"$ref": "#/$defs/search_expression"}},
    "any": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"$ref": "#/$defs/search_expression"}},
}, "description": "Exactly one key: filters (ANDed fields), all (AND children), or any (OR children). Maximum depth 4 and 100 total nodes. Leaves must have nonempty filters."}

_TOOL_DEFINITIONS = [
    _tool("list_categories", "Discover exact committed category names when the user asks about categories or unfamiliar stored names need discovery. Not a prerequisite for known resistor, capacitor or inductor stock, established conversation categories, or exact ordering-code lookups. Do not call for every new batch. Returns part_category, part_count (distinct stock records), and total_quantity (sum of stored quantities). Includes categories whose stock is zero. Pending reviews are excluded. These counts do not establish electrical suitability; search_parts retrieves the relevant stock.", {}),
    _tool("search_parts", "Search committed inventory. For multiple categories, use one expression.any with category-filter branches instead of separate calls; keep shared constraints in top-level filters and paginate the combined query unchanged. Use expression for nested all (AND) / any (OR) groups of filters, combined with top-level filters and minimum_quantity. Check many values in one expression; results contain each matching record once. Expression searches return exact filter matches only, without nearby nominal alternatives. Exact and incomplete pages are ordered by part id; partial matches are ranked by most confirmed requirements, fewer unqualified requirements, fewer mismatches, then normalized numeric shortfall and part id. Start with offset 0; when next_offset is non-null, reuse the same filters, requirements, minimum_quantity, and limit with that offset. Counts cover all results. For specification searches, limit and offset apply separately to matches, incomplete candidates and partial matches (at most 3 * limit records per page). next_offset is null when all groups are exhausted. If inventory or accepted facts change, restart pagination. Known category synonyms (opamp, LDO/linear_regulator, buck_converter, LEDs) match without rewriting stock identity. Nominal values are returned as exact decimal base units in value, with value_unit and display_value. Filters accept decimal base units or explicit-unit labels; include value_unit or explicit units for ambiguous categories. Passive values compare equivalent nominal units (10 kΩ = 10000r, 0.1 µF = 100nF). Package and full part number match exactly; omit an uncertain package and ask for clarification. minimum_quantity is available stock per record. Discover supported specification requirements with get_specification_contract before querying requirements. Returns confirmed matches with supporting source evidence separately from incomplete candidates and explicitly labeled partial_matches. Partial matches retain every failed requirement with its requested value, actual fact and source evidence, plus unresolved qualifiers. With an explicit passive category and value filter, nearby nominal values also appear as partial matches with filter_mismatches; package, full ordering code and minimum stock remain exact. Always call these partial matches and explain each mismatch; never claim they meet all requirements, are interchangeable, or allocate them in a BOM without the user choosing to relax requirements. All qualifiers and conditions must match the specification contract: accepted operating minimum/maximum facts can cover contained requested condition intervals; other bases require equal conditions. Test points, typical values and stress ratings do not establish operating coverage.", {"filters": {"type": "object", "additionalProperties": False, "properties": {key: value for key, value in _FIELDS.items() if key in {"part_category", "profile", "value", "value_unit", "package", "part_number"}}}, "expression": {"$ref": "#/$defs/search_expression"}, "requirements": _REQUIREMENTS_SCHEMA, "minimum_quantity": {"type": "integer", "minimum": 0}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},),
    _tool("search_candidates", "Find candidate stock by whitespace-separated, case-insensitive literal terms across committed descriptions, manufacturers, part numbers, categories, values, and packages. Every term must match the same stock record, possibly in different fields. For example, Nexperia 5350 SOT23 combines manufacturer, part fragment and package. Markings can be found only if recorded. Use search terms, not a natural-language question; punctuation is literal, with no wildcard or quotation operators. Text candidates do not establish nominal-unit equivalence or electrical suitability. Optional filters use search_parts exact identity and nominal-value rules; minimum_quantity applies per record. Results are candidates, not verified identities, interchangeable stock, or evidence of electrical suitability. Preserve every returned ordering suffix and quantity; clarify ambiguous markings before adding or merging stock. Pending proposals are excluded. Results are ordered by id and bounded by limit. Start with offset 0 and follow next_offset with the same query, filters, minimum_quantity, and limit until null. Counts cover all candidates. Restart if inventory changes.", {
        "query": {"type": "string", "minLength": 1},
        "filters": {"type": "object", "additionalProperties": False, "properties": {key: value for key, value in _FIELDS.items() if key in {"part_category", "profile", "value", "value_unit", "package", "part_number"}}},
        "minimum_quantity": {"type": "integer", "minimum": 0},
        "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }, required=["query"]),
    _tool("check_inventory", "Check an entire pasted BOM or part list in one call (1–100 rows). Submit parsed rows as written; deduplication is optional. The server groups identical filters, sums explicit quantities, and returns input_indices so every row remains accounted for. Missing quantities remain unknown: mixed known/unknown duplicates do not establish sufficiency. Full ordering codes preserve commas and suffixes. Generic rows need category and value, plus package only if supplied. An invalid_input error with input_indices identifies all zero-based rows missing that identity; ask targeted clarification for those rows rather than guessing values or reporting them as absent stock. Results give availability per requirement and the most stocked exact match; never sum distinct variants. overlapping_requirements identifies rows that could reuse stock and require allocation clarification. Checks do not reserve or allocate stock. Pending reviews are excluded. Use search_parts only for additional variants or ambiguity; verify electrical suitability separately. No pagination or enrichment is required.", {
        "items": {"type": "array", "minItems": 1, "maxItems": 100, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "filters": {"type": "object", "additionalProperties": False, "properties": {
                    key: {"type": "string", "minLength": 1} for key in ("part_number", "part_category", "value", "value_unit", "package")}},
                "quantity": {"type": "integer", "minimum": 1}},
            "required": ["filters"]}},
    }, required=["items"]),
    _tool("get_part", "Get one committed part by id.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("add_part", "Strict single-record creation with optional automatic enrichment. For ordinary confirmed additions, use add_parts with one item instead: it deduplicates without a preliminary search. Unnumbered stock outside resistor/capacitor/inductor categories always creates a separate record; shared descriptions and packages do not establish identity. Use an explicit add_stock part_id for more of an existing physical group; never invent an ordering code to force merging. Set enrich=false for assortment/kit contents, generic stock, or when the user requests no manufacturer lookup. Omitted or true uses automatic enrichment for eligible discrete/IC parts; passive parts skip automatic lookup. Skipping preserves supplied fields and allows an explicit lookup_part_specs request later.", {**_FIELDS, "enrich": {"type": "boolean"}}, required=["part_category", "profile", "quantity"]),
    _tool("add_parts", "Add and deduplicate 1–100 parts atomically in one call, without a preliminary inventory check. Use this for a single part too, with a one-item array, including identified ICs and generic passives. Each item has its own value, package, quantity and metadata. Normalized existing identities and repeated input identities are skipped; existing quantity, metadata and evidence stay unchanged, including zero-stock records. Generic passives match category/value/package; exact ordering codes retain their suffixes. Unidentified non-passives remain distinct. Invalid items or ambiguous stored identities reject the entire batch. Returns added parts/count, skipped_count and input-ordered items with index, status, reason, duplicate_of (input index), and the committed part. Report added and skipped outcomes. Use add_stock_batch for explicitly additional stock at known IDs. This bulk tool performs no supplier lookup; only use lookup_part_specs separately when the user requests it. Preserve exact codes and user-supplied descriptions; do not invent ratings or counts.", {
        "items": {"type": "array", "minItems": 1, "maxItems": 100, "items": _fields_schema(required=["part_category", "profile", "quantity"])}}, required=["items"]),
    _tool("add_stock_batch", "Increment stock for 1–100 distinct explicit part_ids in one atomic, replay-safe call. Each item has its own positive quantity. Use only for additional physical stock the user wants counted; existing records do not imply new stock. Invalid or missing targets reject the whole batch.", {
        "items": {"type": "array", "minItems": 1, "maxItems": 100, "items": {
            "type": "object", "additionalProperties": False, "properties": {
                "part_id": {"type": "integer", "minimum": 1}, "quantity": {"type": "integer", "minimum": 1}},
            "required": ["part_id", "quantity"]}}}, required=["items"]),
    _tool("update_parts", "Modify 1–100 distinct explicit part_ids with separate fields for each item in one atomic call and one server approval. Omitted fields remain unchanged. Any invalid item, identity conflict or changed approval target rejects the entire batch. Use this for different quantities or metadata across multiple parts.", {
        "items": {"type": "array", "minItems": 1, "maxItems": 100, "items": {
            "type": "object", "additionalProperties": False, "properties": {
                "part_id": {"type": "integer", "minimum": 1}, "fields": _fields_schema(min_properties=1)},
            "required": ["part_id", "fields"]}}}, required=["items"]),
    _tool("add_stock", "Add positive stock to one part.", {"part_id": {"type": "integer", "minimum": 1}, "quantity": {"type": "integer", "minimum": 1}}, required=["part_id", "quantity"]),
    _tool("update_part", "Update explicit fields on one part.", {"part_id": {"type": "integer", "minimum": 1}, "fields": _fields_schema(min_properties=1)}, required=["part_id", "fields"]),
    _tool("bulk_update_parts", "Edit selected parts independently. Returns successful parts and per-part errors; a batch can partially succeed.", {"part_ids": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "integer", "minimum": 1}}, "fields": _fields_schema(min_properties=1)}, required=["part_ids", "fields"]),
    _tool("delete_part", "Delete one identified part.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("lookup_part_specs", "Fetch and stage supplier specifications for review.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool("list_pending_reviews", "List pending base-metadata proposals, not committed fields or electrical-rating reviews. Each fields entry contains the proposed value and an accepted flag used to select review fields; accepted does not mean the proposal has been applied. Use get_part for committed identity and quantity, and get_specifications for pending electrical facts. Filter by value (equivalent nominal units), exact part_number, or part_id; filters match proposed or committed fields. Pages are ordered by part_id; follow next_offset with unchanged arguments until null. Discovery omits provenance; include_provenance requires a single part_id.", {"part_id": {"type": "integer", "minimum": 1}, "value": {"type": "string", "minLength": 1}, "part_number": {"type": "string", "minLength": 1}, "include_provenance": {"type": "boolean"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _tool("apply_review", "Apply a pending review for one part.", {"part_id": {"type": "integer", "minimum": 1}, "updates": _fields_schema()}, required=["part_id"]),
    _tool("reject_review", "Reject a pending review, wholly or by field.", {"part_id": {"type": "integer", "minimum": 1}, "fields": {"type": "array", "items": {"type": "string", "minLength": 1}}}, required=["part_id"]),
    _tool("get_provenance", "Get accepted field provenance for one part.", {"part_id": {"type": "integer", "minimum": 1}}, required=["part_id"]),
    _tool('get_specification_contract', 'Discover supported fields, units, qualifiers, and comparisons for any inventory category. Unsupported categories remain valid inventory.', {'category': {'type': 'string', 'minLength': 1}}, required=['category']),
    _tool('get_specifications', 'Read accepted electrical facts and pending specification review for one exact part. Pending facts are not confirmed.', {'part_id': {'type': 'integer', 'minimum': 1}}, required=['part_id']),
    _tool('list_pending_specification_reviews', 'Discover pending electrical reviews separately from accepted facts and base-metadata reviews. Returns committed part identity and proposed fact names only; use get_specifications with a returned part_id to inspect proposed values, evidence, conditions, and accepted facts before requesting approval. Optional part_category and part_number filters match exact committed fields, preserving ordering suffixes. Pages are ordered by part_id; start with offset 0 and follow next_offset with unchanged filters and limit until null. count covers all matching pending reviews. Restart pagination if reviews or inventory change.', {'part_id': {'type': 'integer', 'minimum': 1}, 'part_category': {'type': 'string', 'minLength': 1}, 'part_number': {'type': 'string', 'minLength': 1}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'offset': {'type': 'integer', 'minimum': 0}}),
    _tool('ingest_datasheet', 'Retrieve a supplied HTTPS manufacturer PDF for one exact inventory part and stage evidenced electrical facts for review. Requires a committed exact ordering code and supported category; clarify unmarked or ambiguous stock first. Preserve suffixes, qualifiers, conditions and quantity. No web discovery or automatic acceptance. Inspect get_specifications and request apply_specification_review for approval. Retrieval failure differs from no_match; do not automatically retry failed paid extraction.', {'part_id': {'type': 'integer', 'minimum': 1}, 'source_url': {'type': 'string', 'minLength': 1}}, required=['part_id', 'source_url']),
    _tool('stage_specification_review', 'Stage explicitly user-asserted electrical facts for review. Quote the user assertion; do not invent source evidence. Assertions do not confirm source-backed search requirements.', {'part_id': {'type': 'integer', 'minimum': 1}, 'facts': _FACTS_SCHEMA}, required=['part_id', 'facts']),
    _tool('apply_specification_review', 'Accept the pending electrical facts for this exact part, preserving source passages, conditions, and evidence kind. Approval does not turn user assertions into source evidence.', {'part_id': {'type': 'integer', 'minimum': 1}}, required=['part_id']),
    _tool('reject_specification_review', 'Discard a pending electrical specification review without changing accepted facts.', {'part_id': {'type': 'integer', 'minimum': 1}}, required=['part_id']),
]

# Definitions live at the tool root so standard JSON Schema references resolve.
next(tool for tool in _TOOL_DEFINITIONS if tool["name"] == "search_parts")["inputSchema"]["$defs"] = {
    "search_expression": _EXPRESSION_SCHEMA}

_APPROVAL_REQUIRED = {"update_parts", "update_part", "bulk_update_parts", "delete_part", "apply_review", "reject_review", "apply_specification_review", "reject_specification_review"}
