"""
Local JLCPCB parts database lookup.

Queries a local copy of the jlcparts cache.sqlite3 by manufacturer part number.
See https://github.com/yaqwsx/jlcparts for the database source and download instructions.

The database is keyed by LCSC number but also contains the manufacturer part number
in the `mfr` column, which we query against.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def lookup_by_mpn(db_path: str | Path, part_number: str) -> dict:
    """
    Look up a part by manufacturer part number.

    Returns {"specs": dict | None, "debug": dict | None, "status": str},
    matching the interface expected by _build_source_attempt in lookup.py.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT lcsc, mfr, manufacturer, package, description, datasheet
                FROM v_components
                WHERE UPPER(mfr) = UPPER(?)
                LIMIT 1
                """,
                (part_number,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return {
            "specs": None,
            "debug": None,
            "status": "failed",
            "error": {"error": str(exc), "error_type": type(exc).__name__},
        }

    if row is None:
        return {"specs": None, "debug": None, "status": "no-match"}

    specs: dict = {}
    if row["mfr"]:
        specs["part_number"] = row["mfr"]
    if row["manufacturer"]:
        specs["manufacturer"] = row["manufacturer"]
    if row["package"]:
        specs["package"] = row["package"]
    if row["description"]:
        specs["description"] = row["description"]

    lcsc_number = f"C{row['lcsc']}"
    debug = {
        "requested_part_number": part_number,
        "manufacturer_part_number": row["mfr"],
        "lcsc_number": lcsc_number,
        "product_url": None,
        "datasheet_url": row["datasheet"] or None,
        "manufacturer": row["manufacturer"],
        "package": row["package"],
    }

    return {"specs": specs, "debug": debug, "status": "ok"}
