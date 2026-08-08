from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from db import persistence

from .errors import DomainError, ErrorCode
from .models import (
    AddPartRequest, AddPartsRequest, AddStockRequest, ApplyReviewRequest, BulkUpdateRequest,
    DeletePartRequest, FetchSpecsRequest, GetPartRequest, Part, PartFields,
    ProvenanceRequest, RejectReviewRequest, SearchPartsRequest,
    UpdatePartRequest, EDITABLE_PART_FIELDS,
)
from .normalization import normalize_part_payload, validate_fields

SpecFetcher = Callable[[str], Awaitable[dict[str, Any]]]
_EDITABLE = EDITABLE_PART_FIELDS


class PartsBinService:
    """The sole owner of inventory identity, validation, and enrichment review rules."""

    def __init__(self, db_path: str | Path, *, spec_fetcher: SpecFetcher | None = None):
        self.db_path = db_path
        self.spec_fetcher = spec_fetcher
        persistence.init_db(db_path)

    @staticmethod
    def should_enrich(part: Mapping[str, Any]) -> bool:
        return bool(
            part.get("part_number")
            and part.get("profile") == "discrete_ic"
            and str(part.get("part_category", "")).lower() not in {"resistor", "capacitor", "inductor"}
        )

    def search(self, request: SearchPartsRequest) -> list[Part]:
        filters = dict(request.filters)
        unknown = set(filters) - {"part_category", "profile", "value", "package", "part_number"}
        if unknown:
            raise DomainError(ErrorCode.INVALID_INPUT, "unsupported search field", details={"fields": sorted(unknown)})
        return [Part.from_row(row) for row in persistence.query(self.db_path, filters)]

    def list(self) -> list[Part]:
        return self.search(SearchPartsRequest())

    def get(self, request: GetPartRequest) -> Part:
        row = persistence.get_by_id(self.db_path, request.part_id)
        if row is None:
            raise DomainError(ErrorCode.PART_NOT_FOUND, "Part not found", details={"part_id": request.part_id})
        return Part.from_row(row)

    def add_part(self, request: AddPartRequest) -> Part:
        fields = validate_fields(vars(request.fields))
        duplicate = persistence.find_duplicate(self.db_path, fields)
        if duplicate is not None:
            raise DomainError(ErrorCode.DUPLICATE_PART, "An identical part already exists", details={"part_id": duplicate["id"]})
        try:
            part_id = persistence.insert_part(self.db_path, fields)
        except Exception as exc:
            if persistence.is_integrity_error(exc):
                raise DomainError(ErrorCode.DUPLICATE_PART, "An identical part already exists") from exc
            raise
        return self.get(GetPartRequest(part_id))

    def add_or_increment(self, request: AddPartRequest) -> Part:
        fields = validate_fields(vars(request.fields))
        duplicate = persistence.find_duplicate(self.db_path, fields)
        if duplicate is not None:
            self._increment_duplicate(duplicate, fields)
            return self.get(GetPartRequest(duplicate["id"]))
        return self.add_part(AddPartRequest(PartFields.from_mapping(fields)))

    def add_parts(self, request: AddPartsRequest) -> list[Part]:
        if not request.items:
            raise DomainError(ErrorCode.INVALID_INPUT, "at least one part is required")
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in request.items:
            fields = validate_fields(vars(item))
            if fields.get("part_number"):
                key = ("part_number", fields["part_number"])
            else:
                key = ("identity", fields.get("part_category"), fields.get("value"), fields.get("package"))
            if key in merged:
                merged[key]["quantity"] += fields["quantity"]
            else:
                merged[key] = fields
        return [self.add_or_increment(AddPartRequest(PartFields.from_mapping(fields))) for fields in merged.values()]

    def duplicate_for_add(self, fields: Mapping[str, Any]) -> Part | None:
        candidate = normalize_part_payload(dict(fields))
        duplicate = persistence.find_duplicate(self.db_path, candidate)
        return Part.from_row(duplicate) if duplicate is not None else None

    def _increment_duplicate(self, duplicate: Mapping[str, Any], fields: Mapping[str, Any]) -> None:
        quantity = fields.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise DomainError(ErrorCode.INVALID_INPUT, "quantity must be a positive integer")
        persistence.increment_stock(self.db_path, duplicate["id"], quantity)

    def add_stock(self, request: AddStockRequest) -> Part:
        if not isinstance(request.quantity, int) or isinstance(request.quantity, bool) or request.quantity <= 0:
            raise DomainError(ErrorCode.INVALID_INPUT, "quantity must be a positive integer")
        self.get(GetPartRequest(request.part_id))
        persistence.increment_stock(self.db_path, request.part_id, request.quantity)
        return self.get(GetPartRequest(request.part_id))

    def update_part(self, request: UpdatePartRequest) -> Part:
        current = self.get(GetPartRequest(request.part_id))
        unknown = set(request.fields) - _EDITABLE
        if unknown:
            raise DomainError(ErrorCode.INVALID_INPUT, "unsupported part field", details={"fields": sorted(unknown)})
        merged = {name: getattr(current, name) for name in _EDITABLE}
        merged.update(request.fields)
        cleaned = validate_fields(merged)
        self._replace_one(request.part_id, cleaned)
        persistence.clear_pending_review(self.db_path, request.part_id)
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
        try:
            persistence.replace_parts_atomic(self.db_path, updates)
        except Exception as exc:
            if persistence.is_integrity_error(exc):
                raise DomainError(ErrorCode.CONFLICT, "Bulk update conflicts with an existing inventory record") from exc
            raise
        for part_id, _ in updates:
            persistence.clear_pending_review(self.db_path, part_id)
        return [self.get(GetPartRequest(part_id)) for part_id in request.part_ids]

    def delete_part(self, request: DeletePartRequest) -> None:
        self.get(GetPartRequest(request.part_id))
        persistence.delete_part(self.db_path, request.part_id)

    async def fetch_and_stage_specs(self, request: FetchSpecsRequest) -> dict[str, Any]:
        part = self.get(GetPartRequest(request.part_id))
        if not part.part_number:
            raise DomainError(ErrorCode.INVALID_INPUT, "Part has no part number to look up")
        if self.spec_fetcher is None:
            raise DomainError(ErrorCode.ENRICHMENT_UNAVAILABLE, "No enrichment provider is configured")
        result = await self.spec_fetcher(part.part_number)
        updates = result.get("chosen_updates", {})
        if updates:
            persistence.save_pending_review(self.db_path, part.id, updates, result.get("durable_provenance", []))
        return {"part": part, **result}

    def list_pending_reviews(self) -> dict[int, dict]:
        return persistence.list_pending_reviews(self.db_path)

    def apply_review(self, request: ApplyReviewRequest) -> Part:
        self.get(GetPartRequest(request.part_id))
        review = self.list_pending_reviews().get(request.part_id)
        if review is None:
            raise DomainError(ErrorCode.REVIEW_NOT_FOUND, "Pending review not found")
        updates = dict(request.updates or {name: item["value"] for name, item in review["fields"].items()})
        if not updates:
            raise DomainError(ErrorCode.INVALID_INPUT, "No updates to apply")
        persistence.update_fields_with_provenance(self.db_path, request.part_id, updates, list(request.provenance) or review["provenance"])
        persistence.clear_pending_review(self.db_path, request.part_id, list(updates))
        return self.get(GetPartRequest(request.part_id))

    def reject_review(self, request: RejectReviewRequest) -> None:
        self.get(GetPartRequest(request.part_id))
        if request.fields:
            review = self.list_pending_reviews().get(request.part_id)
            if review is None:
                raise DomainError(ErrorCode.REVIEW_NOT_FOUND, "Pending review not found")
            persistence.clear_pending_review(self.db_path, request.part_id, list(request.fields))
        else:
            persistence.clear_pending_review(self.db_path, request.part_id)

    def provenance(self, request: ProvenanceRequest) -> list[dict]:
        self.get(GetPartRequest(request.part_id))
        return persistence.list_field_provenance(self.db_path, request.part_id)

    def update_with_provenance(
        self, part_id: int, fields: Mapping[str, Any], provenance: list[Mapping[str, Any]]
    ) -> Part:
        self.get(GetPartRequest(part_id))
        persistence.update_fields_with_provenance(self.db_path, part_id, dict(fields), [dict(item) for item in provenance])
        return self.get(GetPartRequest(part_id))

    def _replace_one(self, part_id: int, fields: dict) -> None:
        try:
            persistence.replace_parts_atomic(self.db_path, [(part_id, fields)])
        except Exception as exc:
            if persistence.is_integrity_error(exc):
                raise DomainError(ErrorCode.CONFLICT, "Update conflicts with an existing inventory record") from exc
            raise


def update_fields_with_provenance(db_path: str | Path, part_id: int, fields: Mapping[str, Any], provenance: list[Mapping[str, Any]]) -> Part:
    """Domain entry point for updating fields together with their provenance."""
    return PartsBinService(db_path).update_with_provenance(part_id, fields, provenance)
