"""Bounded pages over deterministically ordered domain search results."""
from .errors import DomainError, ErrorCode


def validate_page(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError(ErrorCode.INVALID_INPUT, 'Result limit must be between one and one hundred')
    if type(offset) is not int or offset < 0:
        raise DomainError(ErrorCode.INVALID_INPUT, 'Result offset must be a non-negative integer')


def next_offset(count: int, limit: int, offset: int) -> int | None:
    return offset + limit if offset + limit < count else None
