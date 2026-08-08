"""Typed Parts Bin inventory and enrichment domain service."""

from .errors import DomainError, ErrorCode
from .models import (
    AddPartRequest,
    AddPartsRequest,
    AddStockRequest,
    ApplyReviewRequest,
    BulkUpdateRequest,
    DeletePartRequest,
    FetchSpecsRequest,
    GetPartRequest,
    Part,
    PartFields,
    ProvenanceRequest,
    RejectReviewRequest,
    SearchPartsRequest,
    UpdatePartRequest,
    EDITABLE_PART_FIELDS,
    editable_part_fields,
)
from .service import PartsBinService, update_fields_with_provenance

__all__ = [
    "AddPartRequest", "AddPartsRequest", "AddStockRequest", "ApplyReviewRequest",
    "BulkUpdateRequest", "DeletePartRequest", "DomainError", "ErrorCode",
    "FetchSpecsRequest", "GetPartRequest", "Part", "PartFields",
    "PartsBinService", "ProvenanceRequest", "RejectReviewRequest",
    "SearchPartsRequest", "UpdatePartRequest",
    "EDITABLE_PART_FIELDS",
    "update_fields_with_provenance",
    "editable_part_fields",
]
