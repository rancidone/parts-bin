from dataclasses import dataclass, field
from typing import Any, Mapping


EDITABLE_PART_FIELDS = frozenset({
    "part_category", "profile", "value", "package", "part_number",
    "quantity", "manufacturer", "description",
})


def editable_part_fields(fields: Mapping[str, Any], *, omit_none: bool = False) -> dict[str, Any]:
    return {
        key: value
        for key, value in fields.items()
        if key in EDITABLE_PART_FIELDS and (not omit_none or value is not None)
    }


@dataclass(frozen=True)
class PartFields:
    part_category: str
    profile: str
    quantity: int
    value: str | None = None
    package: str | None = None
    part_number: str | None = None
    manufacturer: str | None = None
    description: str | None = None

    @classmethod
    def from_mapping(cls, fields: Mapping[str, Any]) -> "PartFields":
        return cls(**{name: fields.get(name) for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Part:
    id: int
    part_category: str
    profile: str
    value: str | None
    package: str | None
    part_number: str | None
    quantity: int
    manufacturer: str | None
    description: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Part":
        return cls(**{name: row[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class SearchPartsRequest:
    filters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GetPartRequest:
    part_id: int


@dataclass(frozen=True)
class AddPartRequest:
    fields: PartFields


@dataclass(frozen=True)
class AddPartsRequest:
    items: tuple[PartFields, ...]


@dataclass(frozen=True)
class AddStockRequest:
    part_id: int
    quantity: int


@dataclass(frozen=True)
class UpdatePartRequest:
    part_id: int
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class BulkUpdateRequest:
    part_ids: tuple[int, ...]
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class DeletePartRequest:
    part_id: int


@dataclass(frozen=True)
class FetchSpecsRequest:
    part_id: int


@dataclass(frozen=True)
class ProvenanceRequest:
    part_id: int


@dataclass(frozen=True)
class ApplyReviewRequest:
    part_id: int
    updates: Mapping[str, Any] | None = None
    provenance: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class RejectReviewRequest:
    part_id: int
    fields: tuple[str, ...] | None = None
