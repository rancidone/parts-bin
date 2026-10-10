"""Inventory presentation formats, independent of the persistence adapter."""

import csv
import io
from collections.abc import Iterable, Mapping
from typing import Any


def export_csv(rows: Iterable[Mapping[str, Any]]) -> str:
    """Serialize inventory rows using the existing download column order."""
    fields = ["part_category", "value", "package", "quantity", "part_number", "manufacturer", "description"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()
