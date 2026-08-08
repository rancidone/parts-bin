from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    PART_NOT_FOUND = "part_not_found"
    DUPLICATE_PART = "duplicate_part"
    AMBIGUOUS_TARGET = "ambiguous_target"
    CONFLICT = "conflict"
    REVIEW_NOT_FOUND = "review_not_found"
    ENRICHMENT_UNAVAILABLE = "enrichment_unavailable"
    APPROVAL_REQUIRED = "approval_required"


class DomainError(Exception):
    """A stable, transport-neutral domain failure."""

    def __init__(self, code: ErrorCode, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
