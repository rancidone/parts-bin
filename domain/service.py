from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from typing import Any

from .repositories import PartsBinRepository, RepositoryConflict

from .errors import DomainError, ErrorCode
from .models import (
    AddPartRequest, AddPartsRequest, AddStockRequest, AdjustStockRequest, ApplyReviewRequest, BulkUpdateRequest,
    CategorySummary, DeletePartRequest, FetchSpecsRequest, GetPartRequest, Part, PartFields,
    ProvenanceRequest, RejectReviewRequest, SearchPartsRequest, SearchCandidatesRequest,
    UpdatePartRequest, IngestDatasheetRequest, EDITABLE_PART_FIELDS,
    CheckInventoryRequest,
)
from .normalization import normalize_part_payload, part_identity, validate_fields, clean_text

from .search import values_match, nominal_value, search_category
from . import specifications
from .pagination import validate_page, next_offset

SpecFetcher = Callable[[str], Awaitable[dict[str, Any]]]
DatasheetFetcher = Callable[[Part, str], Awaitable[dict[str, Any]]]
_EDITABLE = EDITABLE_PART_FIELDS


class PartsBinService:
    """The sole owner of inventory identity, validation, and enrichment review rules."""

    def __init__(self, repository: PartsBinRepository, *, spec_fetcher: SpecFetcher | None = None,
                 datasheet_fetcher: DatasheetFetcher | None = None):
        self.repository = repository
        self.spec_fetcher = spec_fetcher
        self.datasheet_fetcher = datasheet_fetcher

    @contextmanager
    def transaction(self):
        """Bind domain operations to the repository's unit of work."""
        with self.repository.transaction() as repository:
            yield PartsBinService(repository, spec_fetcher=self.spec_fetcher,
                                  datasheet_fetcher=self.datasheet_fetcher), repository

    def approval_snapshot(self, part_ids: tuple[int, ...]) -> dict:
        """Bind an approval to committed targets and their staged evidence."""
        reviews = self.list_pending_reviews()
        specification_reviews = self.repository.inventory.specification_reviews()
        parts = {part_id: self.repository.inventory.get(part_id) for part_id in part_ids}
        return {str(part_id): {"part": None if part is None else vars(part),
                              "review": reviews.get(part_id),
                              "specifications": self.repository.inventory.specifications(part_id),
                              "specification_review": specification_reviews.get(part_id)} for part_id, part in parts.items()}

    @staticmethod
    def should_enrich(part: Mapping[str, Any], *, enabled: bool = True) -> bool:
        return bool(
            enabled
            and part.get("part_number")
            and part.get("profile") == "discrete_ic"
            and str(part.get("part_category", "")).lower() not in {"resistor", "capacitor", "inductor"}
        )

    def list_categories(self) -> list[CategorySummary]:
        """Discover exact committed category names, including out-of-stock records."""
        return self.repository.inventory.list_categories()

    def search(self, request: SearchPartsRequest) -> list[Part]:
        if (not isinstance(request.minimum_quantity, int) or isinstance(request.minimum_quantity, bool)
                or request.minimum_quantity < 0):
            raise DomainError(ErrorCode.INVALID_INPUT, "minimum_quantity must be a non-negative integer")
        filters = {name: clean_text(value) for name, value in request.filters.items()}
        unknown = set(filters) - {"part_category", "profile", "value", "package", "part_number"}
        if unknown:
            raise DomainError(ErrorCode.INVALID_INPUT, "unsupported search field", details={"fields": sorted(unknown)})
        if any(value is not None and not isinstance(value, str) for value in filters.values()):
            raise DomainError(ErrorCode.INVALID_INPUT, "Search filters must be strings or null")
        # Historical edits can contain unnormalized spellings. Compare values in
        # the domain without rewriting stored rows, timestamps, or evidence.
        category = filters.get("part_category")
        category_alias = category is not None and search_category(category) in {
            "operational amplifier", "bjt", "mosfet", "diode", "transistor"}
        if category_alias:
            filters.pop("part_category")
        value = filters.pop("value", None)
        candidates = self.repository.inventory.search(filters)
        if category_alias:
            candidates = [part for part in candidates
                          if search_category(part.part_category) == search_category(category)]
        if value is None:
            return [part for part in candidates
                    if part.quantity >= request.minimum_quantity]
        matches = []
        for part in candidates:
            if part.value is None or part.quantity < request.minimum_quantity:
                continue
            if part.part_category.lower() in {"resistor", "capacitor", "inductor"}:
                matched = values_match(part.value, value, part.part_category)
            else:
                matched = part.value == value
            if matched:
                matches.append(part)
        return matches

    def search_candidates(self, request: SearchCandidatesRequest) -> list[Part]:
        """Literal text discovery only; candidates do not establish stock identity.

        Markings are searchable when recorded in a description or part number.
        Search committed fields only, leaving pending proposals and stock untouched.
        """
        if not isinstance(request.query, str) or not 1 <= len(request.query.strip()) <= 200:
            raise DomainError(ErrorCode.INVALID_INPUT, "Candidate query must contain one to two hundred characters")
        query = request.query.strip().casefold()
        parts = self.search(SearchPartsRequest(request.filters, request.minimum_quantity))
        return sorted((part for part in parts if any(
            query in (text or "").casefold()
            for text in (part.description, part.manufacturer, part.part_number)
        )), key=lambda part: part.id)

    def search_page(self, request: SearchPartsRequest, *, limit: int = 20, offset: int = 0) -> dict:
        validate_page(limit, offset)
        rows = sorted(self.search(request), key=lambda part: part.id)
        following = next_offset(len(rows), limit, offset)
        return {"parts": rows[offset:offset + limit], "count": len(rows),
                "truncated": following is not None, "next_offset": following}

    def candidate_page(self, request: SearchCandidatesRequest, *, limit: int = 20, offset: int = 0) -> dict:
        validate_page(limit, offset)
        rows = self.search_candidates(request)
        following = next_offset(len(rows), limit, offset)
        return {"candidates": rows[offset:offset + limit], "count": len(rows),
                "truncated": following is not None, "next_offset": following,
                "match_kind": "candidate"}

    def check_inventory(self, request: CheckInventoryRequest, *, limit: int = 20, offset: int = 0) -> dict:
        """Check independent BOM lines without reserving or consuming stock.

        A quantity is covered only by one matching stock record. Distinct
        ordering codes or packages are never assumed to be interchangeable.
        """
        if not 1 <= len(request.items) <= 100:
            raise DomainError(ErrorCode.INVALID_INPUT, "Check one to one hundred inventory items")
        validate_page(limit, offset)
        if limit > 20:
            raise DomainError(ErrorCode.INVALID_INPUT, "Inventory check pages contain at most twenty items")
        results = []
        for index, item in enumerate(request.items):
            filters = dict(item.filters)
            if any(not isinstance(value, str) or not value.strip() for value in filters.values()):
                raise DomainError(ErrorCode.INVALID_INPUT, "Inventory check filters must be non-empty strings")
            if not filters.get("part_number") and not (filters.get("part_category") and filters.get("value")):
                raise DomainError(ErrorCode.INVALID_INPUT, "Each item needs a part number or a category and value")
            if item.quantity is not None and (type(item.quantity) is not int or item.quantity < 1):
                raise DomainError(ErrorCode.INVALID_INPUT, "Requested quantity must be a positive integer")
            parts = self.search(SearchPartsRequest(filters))
            available = max((part.quantity for part in parts), default=0)
            if not parts:
                status = "missing"
            elif available == 0:
                status = "out_of_stock"
            elif item.quantity is None:
                status = "in_stock"
            else:
                status = "sufficient" if available >= item.quantity else "insufficient"
            # Put the most stocked record first so a covering record is visible
            # even when the compact response omits additional variants.
            ordered = sorted(parts, key=lambda part: (-part.quantity, part.id))
            results.append({"index": index, "filters": filters, "requested_quantity": item.quantity,
                            "status": status, "max_available_quantity": available,
                            "shortage": None if item.quantity is None else max(0, item.quantity - available),
                            "match_count": len(parts), "parts": ordered[:1],
                            "truncated": len(parts) > 1})
        return {"items": results[offset:offset + limit], "count": len(results),
                "next_offset": next_offset(len(results), limit, offset), "stock_reserved": False}

    def search_specifications(self, request: SearchPartsRequest, requirements: list[dict], *, limit: int = 20, offset: int = 0) -> dict:
        category = request.filters.get('part_category')
        if not isinstance(category, str):
            raise DomainError(ErrorCode.INVALID_INPUT, 'Specification search requires one explicit category')
        validate_page(limit, offset)
        checked = specifications.validate_requirements(category, requirements)
        matches, incomplete = [], []
        for part in sorted(self.search(request), key=lambda part: part.id):
            eligible, missing, supporting = specifications.evaluate(
                category, self.repository.inventory.specifications(part.id), checked)
            if not eligible:
                continue
            if missing:
                incomplete.append({'part': vars(part), 'missing_or_unqualified': missing})
            else:
                matches.append({'part': vars(part), 'supporting_facts': supporting})
        following = next_offset(max(len(matches), len(incomplete)), limit, offset)
        return {'matches': matches[offset:offset + limit], 'incomplete': incomplete[offset:offset + limit],
                'match_count': len(matches), 'incomplete_count': len(incomplete),
                'truncated': following is not None, 'next_offset': following}

    def get_specifications(self, part_id: int) -> dict:
        part = self.get(GetPartRequest(part_id))
        return {'part_id': part_id, 'part_number': part.part_number,
                'facts': self.repository.inventory.specifications(part_id),
                'pending_review': self.repository.inventory.specification_reviews().get(part_id)}

    def specification_review_page(self, *, part_id: int | None = None,
                                  part_category: str | None = None, part_number: str | None = None,
                                  limit: int = 20, offset: int = 0) -> dict:
        """Discover pending electrical reviews without returning evidence or snapshots."""
        validate_page(limit, offset)
        if part_id is not None and (type(part_id) is not int or part_id < 1):
            raise DomainError(ErrorCode.INVALID_INPUT, 'part_id must be a positive integer')
        for value in (part_category, part_number):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise DomainError(ErrorCode.INVALID_INPUT, 'Review filters must be non-empty strings')
        rows = []
        for identifier, review in sorted(self.repository.inventory.specification_reviews().items()):
            if part_id is not None and identifier != part_id:
                continue
            part = self.get(GetPartRequest(identifier))
            if part_category is not None and part.part_category != part_category:
                continue
            if part_number is not None and part.part_number != part_number:
                continue
            rows.append({'part_id': identifier, 'part_category': part.part_category,
                         'part_number': part.part_number,
                         'fact_names': [fact['name'] for fact in review['facts']]})
        following = next_offset(len(rows), limit, offset)
        return {'reviews': rows[offset:offset + limit], 'count': len(rows),
                'truncated': following is not None, 'next_offset': following}

    def stage_specifications(self, original: Part, facts: list[dict]) -> None:
        checked = specifications.validate_facts(original.part_category, facts, part_number=original.part_number)
        nominal_field = {'resistor': 'resistance', 'capacitor': 'capacitance', 'inductor': 'inductance'}.get(original.part_category.lower())
        for fact in checked:
            if fact['name'] == nominal_field and original.value is not None:
                stored = nominal_value(original.value, original.part_category)
                proposed = specifications.numeric_value(fact['value'], specifications.definition(original.part_category, fact['name']).unit)
                if stored is not None and stored != proposed:
                    raise DomainError(ErrorCode.CONFLICT, 'Proposed nominal specification conflicts with committed value')
        existing = self.repository.inventory.specifications(original.id)
        if not self.repository.inventory.stage_specifications(original, checked, existing):
            raise DomainError(ErrorCode.CONFLICT, 'Identity, specifications, or pending specification review changed')

    async def ingest_datasheet(self, request: IngestDatasheetRequest) -> dict:
        original = self.get(GetPartRequest(request.part_id))
        if not original.part_number:
            raise DomainError(ErrorCode.INVALID_INPUT, 'An exact inventory ordering code is required; clarify identity first')
        if not specifications.contract(original.part_category)['supported']:
            if search_category(original.part_category) == 'transistor':
                raise DomainError(ErrorCode.INVALID_INPUT,
                                  'Clarify the transistor subtype and explicitly correct its category to bjt or mosfet before electrical extraction')
            raise DomainError(ErrorCode.INVALID_INPUT, 'This category has no electrical specification contract')
        if not isinstance(request.source_url, str) or not 1 <= len(request.source_url) <= 2048:
            raise DomainError(ErrorCode.INVALID_INPUT, 'A bounded supplied source URL is required')
        before = self.get_specifications(original.id)
        if before['pending_review'] is not None:
            raise DomainError(ErrorCode.CONFLICT, 'Resolve the pending specification review before another extraction')
        if self.datasheet_fetcher is None:
            raise DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE, 'Supplied datasheet extraction is not configured')
        candidate = await self.datasheet_fetcher(original, request.source_url)
        outcome = candidate['outcome']
        if outcome not in {'proposal', 'needs_clarification', 'no_match'}:
            raise DomainError(ErrorCode.INVALID_INPUT, 'Invalid datasheet extraction outcome')
        facts = candidate['facts']
        if outcome != 'proposal' and facts:
            raise DomainError(ErrorCode.INVALID_INPUT, 'Unresolved identity must not stage facts')
        if facts:
            # Retrieval takes place outside the unit of work. Bind staging to both
            # the original identity and accepted facts seen before retrieval.
            with self.transaction() as (service, _):
                if service.get_specifications(original.id)['facts'] != before['facts']:
                    raise DomainError(ErrorCode.CONFLICT, 'Accepted specifications changed during extraction')
                service.stage_specifications(original, facts)
        return {'part_id': original.id, 'outcome': outcome,
                'clarification': candidate.get('clarification'), 'review_staged': bool(facts),
                'extraction_assessment': candidate.get('extraction_assessment'),
                **self.get_specifications(original.id)}

    def apply_specification_review(self, part_id: int) -> dict:
        part = self.get(GetPartRequest(part_id))
        review = self.repository.inventory.specification_reviews().get(part_id)
        if review is None:
            raise DomainError(ErrorCode.REVIEW_NOT_FOUND, 'Pending specification review not found')
        specifications.validate_facts(part.part_category, review['facts'], part_number=part.part_number)
        try:
            self.repository.inventory.apply_specification_review(part_id)
        except RepositoryConflict as exc:
            raise DomainError(ErrorCode.CONFLICT, 'Specification review target changed') from exc
        return self.get_specifications(part_id)

    def reject_specification_review(self, part_id: int) -> None:
        self.get(GetPartRequest(part_id))
        self.repository.inventory.reject_specification_review(part_id)

    def list(self) -> list[Part]:
        return self.search(SearchPartsRequest())

    def get(self, request: GetPartRequest) -> Part:
        row = self.repository.inventory.get(request.part_id)
        if row is None:
            raise DomainError(ErrorCode.PART_NOT_FOUND, "Part not found", details={"part_id": request.part_id})
        return row

    def add_part(self, request: AddPartRequest) -> Part:
        fields = validate_fields(vars(request.fields))
        duplicate = self.duplicate_for_add(fields)
        if duplicate is not None:
            raise DomainError(ErrorCode.DUPLICATE_PART, "An identical part already exists", details={"part_id": duplicate.id})
        try:
            part_id = self.repository.inventory.insert(fields)
        except RepositoryConflict as exc:
            raise DomainError(ErrorCode.DUPLICATE_PART, "An identical part already exists") from exc
        return self.get(GetPartRequest(part_id))

    def add_or_increment(self, request: AddPartRequest) -> Part:
        fields = validate_fields(vars(request.fields))
        duplicate = self.duplicate_for_add(fields)
        if duplicate is not None:
            self._increment_duplicate(duplicate, fields)
            return self.get(GetPartRequest(duplicate.id))
        return self.add_part(AddPartRequest(PartFields.from_mapping(fields)))

    def add_parts(self, request: AddPartsRequest) -> list[Part]:
        if not request.items:
            raise DomainError(ErrorCode.INVALID_INPUT, "at least one part is required")
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in request.items:
            fields = validate_fields(vars(item))
            key = part_identity(fields)
            if key in merged:
                merged[key]["quantity"] += fields["quantity"]
            else:
                merged[key] = fields
        return [self.add_or_increment(AddPartRequest(PartFields.from_mapping(fields))) for fields in merged.values()]

    def duplicate_for_add(self, fields: Mapping[str, Any]) -> Part | None:
        candidate = normalize_part_payload(dict(fields))
        if candidate.get("part_number") is not None:
            filters = {"part_number": candidate["part_number"]}
        else:
            filters = {name: candidate.get(name) for name in ("part_category", "value", "package")}
        matches = [part for part in self.search(SearchPartsRequest(filters))
                   if part_identity(vars(part)) == part_identity(candidate)]
        if len(matches) > 1:
            raise DomainError(ErrorCode.CONFLICT, "Multiple inventory records have the same normalized identity",
                              details={"part_ids": [part.id for part in matches]})
        return matches[0] if matches else None

    def _increment_duplicate(self, duplicate: Part, fields: Mapping[str, Any]) -> None:
        if (self.repository.inventory.specifications(duplicate.id) or
                duplicate.id in self.repository.inventory.specification_reviews()):
            raise DomainError(ErrorCode.CONFLICT, 'Reviewed specification variants require an explicit add_stock target')
        quantity = fields.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise DomainError(ErrorCode.INVALID_INPUT, "quantity must be a positive integer")
        self.repository.inventory.increment_stock(duplicate.id, quantity)

    def add_stock(self, request: AddStockRequest) -> Part:
        if not isinstance(request.quantity, int) or isinstance(request.quantity, bool) or request.quantity <= 0:
            raise DomainError(ErrorCode.INVALID_INPUT, "quantity must be a positive integer")
        self.get(GetPartRequest(request.part_id))
        self.repository.inventory.increment_stock(request.part_id, request.quantity)
        return self.get(GetPartRequest(request.part_id))

    def adjust_stock(self, request: AdjustStockRequest) -> Part:
        if not isinstance(request.delta, int) or isinstance(request.delta, bool) or request.delta not in {-1, 1}:
            raise DomainError(ErrorCode.INVALID_INPUT, "stock adjustment must be -1 or 1")
        with self.transaction() as (service, repository):
            current = service.get(GetPartRequest(request.part_id))
            if current.quantity + request.delta < 0:
                raise DomainError(ErrorCode.INVALID_INPUT, "quantity cannot be negative")
            repository.inventory.increment_stock(request.part_id, request.delta)
            return service.get(GetPartRequest(request.part_id))

    def update_part(self, request: UpdatePartRequest) -> Part:
        current = self.get(GetPartRequest(request.part_id))
        unknown = set(request.fields) - _EDITABLE
        if unknown:
            raise DomainError(ErrorCode.INVALID_INPUT, "unsupported part field", details={"fields": sorted(unknown)})
        merged = {name: getattr(current, name) for name in _EDITABLE}
        merged.update(request.fields)
        cleaned = validate_fields(merged)
        self._replace_one(request.part_id, cleaned)
        self.repository.inventory.clear_pending_review(request.part_id)
        return self.get(GetPartRequest(request.part_id))

    def bulk_update(self, request: BulkUpdateRequest) -> list[Part]:
        if not request.part_ids or len(set(request.part_ids)) != len(request.part_ids):
            raise DomainError(ErrorCode.INVALID_INPUT, "bulk selection must contain distinct part ids")
        if not request.fields:
            raise DomainError(ErrorCode.INVALID_INPUT, "bulk update fields are required")
        rows = [self.get(GetPartRequest(part_id)) for part_id in request.part_ids]
        updates: list[tuple[int, dict]] = []
        for row in rows:
            merged = {name: getattr(row, name) for name in _EDITABLE}
            merged.update(request.fields)
            updates.append((row.id, validate_fields(merged)))
        self._check_replacement_identities(updates)
        try:
            self.repository.inventory.replace_parts(updates)
        except RepositoryConflict as exc:
            raise DomainError(ErrorCode.CONFLICT, "Bulk update conflicts with an existing inventory record") from exc
        for part_id, _ in updates:
            self.repository.inventory.clear_pending_review(part_id)
        return [self.get(GetPartRequest(part_id)) for part_id in request.part_ids]

    def delete_part(self, request: DeletePartRequest) -> None:
        self.get(GetPartRequest(request.part_id))
        self.repository.inventory.delete(request.part_id)

    async def fetch_and_stage_specs(self, request: FetchSpecsRequest) -> dict[str, Any]:
        part = self.get(GetPartRequest(request.part_id))
        if not part.part_number:
            raise DomainError(ErrorCode.INVALID_INPUT, "Part has no part number to look up")
        if self.spec_fetcher is None:
            raise DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE, "No enrichment provider is configured")
        result = await self.spec_fetcher(part.part_number)
        updates = result.get("chosen_updates", {})
        if updates:
            self.repository.inventory.save_pending_review(part.id, updates, result.get("durable_provenance", []))
        return {"part": part, **result}

    async def refresh_specs(self, request: FetchSpecsRequest) -> dict[str, Any]:
        """Refresh from a saved datasheet, or discover metadata and its source link."""
        part = self.get(GetPartRequest(request.part_id))
        result = ({'part': part, 'chosen_updates': {}, 'durable_provenance': [], 'outcome': 'source_refresh'}
                  if part.datasheet_url else await self.fetch_and_stage_specs(request))
        source_url = part.datasheet_url or result.get('chosen_updates', {}).get('datasheet_url')
        electrical = self.get_specifications(part.id)
        if electrical['pending_review'] is not None:
            electrical.update(outcome='pending_review', clarification='Resolve the pending electrical review before refreshing.')
        elif not specifications.contract(part.part_category)['supported']:
            electrical.update(outcome='unsupported', clarification='Electrical extraction is not defined for this category. The datasheet link remains available.')
        elif not source_url:
            electrical.update(outcome='needs_source', clarification='Add a manufacturer datasheet link in Edit part, then fetch specs again.')
        else:
            try:
                electrical = await self.ingest_datasheet(IngestDatasheetRequest(part.id, source_url))
            except DomainError as exc:
                if exc.code == ErrorCode.CONFLICT:
                    raise
                electrical.update(outcome='retrieval_failure' if exc.code == ErrorCode.ENRICHMENT_UNAVAILABLE else 'needs_clarification',
                                  clarification=exc.message)
        return {**result, 'electrical': electrical}

    def list_pending_reviews(self) -> dict[int, dict]:
        return self.repository.inventory.list_pending_reviews()

    def pending_review_page(self, *, part_id: int | None = None, value: str | None = None,
                            part_number: str | None = None, limit: int = 20, offset: int = 0,
                            include_provenance: bool = False) -> dict:
        """Discover proposed or current identities without dumping the backlog."""
        validate_page(limit, offset)
        if include_provenance and part_id is None:
            raise DomainError(ErrorCode.INVALID_INPUT, 'Review provenance requires one part_id')
        rows = []
        for identifier, review in sorted(self.list_pending_reviews().items()):
            if part_id is not None and identifier != part_id:
                continue
            part = self.get(GetPartRequest(identifier))
            proposed = {name: field['value'] for name, field in review['fields'].items()}
            if part_number is not None and part_number not in (part.part_number, proposed.get('part_number')):
                continue
            if value is not None and not any(
                candidate is not None and values_match(candidate, value, category)
                for candidate in (part.value, proposed.get('value'))
                for category in (part.part_category, proposed.get('part_category', part.part_category))
            ):
                continue
            summary = {'part_id': identifier, 'fields': review['fields'], 'outcome': review['outcome']}
            if include_provenance:
                summary['provenance'] = review['provenance']
            rows.append(summary)
        following = next_offset(len(rows), limit, offset)
        return {'reviews': rows[offset:offset + limit], 'count': len(rows),
                'truncated': following is not None, 'next_offset': following}

    def stage_enrichment(self, original: Part, updates: Mapping[str, Any], provenance: list[dict]) -> None:
        """Stage evidenced metadata only if identity and existing review are unchanged."""
        allowed = {"part_number", "manufacturer", "package", "description"}
        if not updates or set(updates) - allowed:
            raise DomainError(ErrorCode.INVALID_INPUT, "Enrichment may propose metadata only")
        if updates.get("part_number", original.part_number) != original.part_number:
            raise DomainError(ErrorCode.CONFLICT, "Enrichment must preserve exact part identity")
        by_field = {record["field_name"]: record for record in provenance}
        for name, value in updates.items():
            if (not isinstance(value, str) or not value.strip() or name not in by_field
                    or by_field[name].get("field_value") != value
                    or not by_field[name].get("evidence")):
                raise DomainError(ErrorCode.INVALID_INPUT, "Enrichment requires evidence for each proposed field")
        if not self.repository.inventory.stage_enrichment_if_unchanged(original, dict(updates), provenance):
            raise DomainError(ErrorCode.CONFLICT, "Part metadata or pending review changed during enrichment")

    def apply_review(self, request: ApplyReviewRequest) -> Part:
        self.get(GetPartRequest(request.part_id))
        review = self.list_pending_reviews().get(request.part_id)
        if review is None:
            raise DomainError(ErrorCode.REVIEW_NOT_FOUND, "Pending review not found")
        updates = dict(request.updates or {name: item["value"] for name, item in review["fields"].items()})
        if not updates:
            raise DomainError(ErrorCode.INVALID_INPUT, "No updates to apply")
        result = self.update_with_provenance(request.part_id, updates, list(request.provenance) or review["provenance"])
        self.repository.inventory.clear_pending_review(request.part_id, list(updates))
        return result

    def reject_review(self, request: RejectReviewRequest) -> None:
        self.get(GetPartRequest(request.part_id))
        if request.fields:
            review = self.list_pending_reviews().get(request.part_id)
            if review is None:
                raise DomainError(ErrorCode.REVIEW_NOT_FOUND, "Pending review not found")
            self.repository.inventory.clear_pending_review(request.part_id, list(request.fields))
        else:
            self.repository.inventory.clear_pending_review(request.part_id)

    def provenance(self, request: ProvenanceRequest) -> list[dict]:
        self.get(GetPartRequest(request.part_id))
        return self.repository.inventory.list_provenance(request.part_id)

    def update_with_provenance(
        self, part_id: int, fields: Mapping[str, Any], provenance: list[Mapping[str, Any]]
    ) -> Part:
        current = self.get(GetPartRequest(part_id))
        unknown = set(fields) - (_EDITABLE - {"quantity"})
        if unknown:
            raise DomainError(ErrorCode.INVALID_INPUT, "unsupported provenance update field",
                              details={"fields": sorted(unknown)})
        # These metadata writes historically omit null rather than clearing it.
        updates = {name: value for name, value in fields.items() if value is not None}
        cleaned = validate_fields({**vars(current), **updates})
        updates = {name: cleaned[name] for name in updates}
        if "part_category" in updates and cleaned["value"] != current.value:
            updates["value"] = cleaned["value"]
        self._check_replacement_identities([(part_id, {**vars(current), **updates})])
        try:
            self.repository.inventory.update_with_provenance(part_id, updates, [dict(item) for item in provenance])
        except RepositoryConflict as exc:
            raise DomainError(ErrorCode.CONFLICT, "Update conflicts with an existing inventory record") from exc
        return self.get(GetPartRequest(part_id))

    def _check_replacement_identities(self, updates: list[tuple[int, dict]]) -> None:
        replacements = dict(updates)
        identities = {}
        # Compare final identities across the batch before writing any row.
        for part in self.list():
            identity = part_identity(replacements.get(part.id, vars(part)))
            identities.setdefault(identity, []).append(part.id)
        for part_id, fields in updates:
            current = self.get(GetPartRequest(part_id))
            identity_fields = ('part_category', 'profile', 'value', 'package', 'part_number', 'manufacturer')
            if any(fields.get(key, getattr(current, key)) != getattr(current, key) for key in identity_fields):
                if (self.repository.inventory.specifications(part_id) or
                        part_id in self.repository.inventory.specification_reviews()):
                    raise DomainError(ErrorCode.CONFLICT, 'Electrical evidence is bound to this identity; create a distinct record')
            collisions = identities.get(part_identity(fields), [])
            if len(collisions) > 1:
                raise DomainError(ErrorCode.CONFLICT, "Update conflicts with an existing inventory record",
                                  details={"part_ids": collisions})

    def _replace_one(self, part_id: int, fields: dict) -> None:
        self._check_replacement_identities([(part_id, fields)])
        try:
            self.repository.inventory.replace_parts([(part_id, fields)])
        except RepositoryConflict as exc:
            raise DomainError(ErrorCode.CONFLICT, "Update conflicts with an existing inventory record") from exc


def update_fields_with_provenance(repository: PartsBinRepository, part_id: int, fields: Mapping[str, Any], provenance: list[Mapping[str, Any]]) -> Part:
    """Domain entry point for updating fields together with their provenance."""
    return PartsBinService(repository).update_with_provenance(part_id, fields, provenance)
