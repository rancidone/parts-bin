"""
MCP tools for inventory search and management.

Mounted at /mcp on the main FastAPI app (see server.py). Tools are thin
wrappers around db.persistence / ingestion.lookup — no new business logic.
"""

from collections.abc import Callable
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from db.persistence import delete_part as _delete_part
from db.persistence import get_by_id, list_all, query, replace_part, upsert
from ingestion.lookup import fetch_specs_detailed


def build_mcp_server(
    get_db_path: Callable[[], str | Path],
    digikey_creds: dict | None,
    jlcparts_db_path: str | None,
) -> FastMCP:
    # Mounted at /mcp on the parent app (see server.py) — serve at "/" here so the
    # combined path is /mcp, not /mcp/mcp.
    mcp = FastMCP("parts-bin", streamable_http_path="/")

    @mcp.tool()
    def search_parts(
        part_category: str | None = None,
        profile: str | None = None,
        value: str | None = None,
        package: str | None = None,
        part_number: str | None = None,
    ) -> list[dict]:
        """Search inventory by structured attributes. Omitted fields are wildcards; call with no arguments to list everything."""
        attrs = {
            "part_category": part_category,
            "profile": profile,
            "value": value,
            "package": package,
            "part_number": part_number,
        }
        if not any(attrs.values()):
            return list_all(get_db_path())
        return query(get_db_path(), attrs)

    @mcp.tool()
    def get_part(part_id: int) -> dict:
        """Fetch a single part by its id."""
        part = get_by_id(get_db_path(), part_id)
        if part is None:
            raise ValueError(f"part {part_id} not found")
        return part

    @mcp.tool()
    def add_part(
        part_category: str,
        profile: str,
        quantity: int,
        value: str | None = None,
        package: str | None = None,
        part_number: str | None = None,
        manufacturer: str | None = None,
        description: str | None = None,
    ) -> dict:
        """
        Add a part to inventory, or increment quantity if an identical part already exists.

        profile must be 'passive' or 'discrete_ic'.
        Returns the resulting part row.
        """
        part_id = upsert(
            get_db_path(),
            {
                "part_category": part_category,
                "profile": profile,
                "quantity": quantity,
                "value": value,
                "package": package,
                "part_number": part_number,
                "manufacturer": manufacturer,
                "description": description,
            },
        )
        return get_by_id(get_db_path(), part_id)

    @mcp.tool()
    def update_part(
        part_id: int,
        part_category: str | None = None,
        profile: str | None = None,
        value: str | None = None,
        package: str | None = None,
        part_number: str | None = None,
        quantity: int | None = None,
        manufacturer: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Update fields on an existing part (including quantity). Omitted fields are left unchanged."""
        if get_by_id(get_db_path(), part_id) is None:
            raise ValueError(f"part {part_id} not found")
        fields = {
            "part_category": part_category,
            "profile": profile,
            "value": value,
            "package": package,
            "part_number": part_number,
            "quantity": quantity,
            "manufacturer": manufacturer,
            "description": description,
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        replace_part(get_db_path(), part_id, fields)
        return get_by_id(get_db_path(), part_id)

    @mcp.tool()
    def delete_part(part_id: int) -> str:
        """Delete a part from inventory by id."""
        if not _delete_part(get_db_path(), part_id):
            raise ValueError(f"part {part_id} not found")
        return f"deleted part {part_id}"

    @mcp.tool()
    async def lookup_part_specs(part_number: str) -> dict:
        """
        Look up spec fields for a part number via Digikey/JLCParts. Read-only —
        does not modify inventory. Use add_part or update_part to save results.
        """
        result = await fetch_specs_detailed(
            part_number,
            digikey_credentials=digikey_creds,
            jlcparts_db_path=jlcparts_db_path,
        )
        return result

    return mcp
