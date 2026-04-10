"""Tests for server HTTP endpoints."""

import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from starlette.testclient import TestClient

import server
from db.persistence import init_db, list_field_provenance, list_pending_reviews, save_pending_review, upsert


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with patch.object(server, "_DB_PATH", db_path):
        with TestClient(server.app, raise_server_exceptions=True) as c:
            yield c, db_path


def _jpeg_bytes(w=100, h=100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h)).save(buf, format="JPEG")
    return buf.getvalue()


class TestHealthEndpoint:
    def test_returns_ok(self, client):
        c, _ = client
        resp = c.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestJlcpartsEndpoints:
    def test_status_reports_missing_configured_db(self, client, tmp_path):
        c, _ = client
        missing_db = tmp_path / "jlcparts.sqlite3"
        with patch.object(server, "_JLCPARTS_DB_PATH", str(missing_db)):
            resp = c.get("/jlcparts/status")
        assert resp.status_code == 200
        assert resp.json() == {"status": "missing", "path": str(missing_db)}

    @pytest.mark.asyncio
    async def test_run_jlcparts_download_awaits_download_coroutine(self):
        with patch.object(server, "_JLCPARTS_DB_PATH", "jlcparts.sqlite3"):
            with patch("ingestion.jlcparts_download.download_if_missing", AsyncMock()) as download:
                await server._run_jlcparts_download()
        download.assert_awaited_once()


class TestInventoryEndpoint:
    def test_empty_inventory(self, client):
        c, _ = client
        resp = c.get("/inventory")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_upserted_parts(self, client):
        c, db = client
        upsert(db, {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })
        resp = c.get("/inventory")
        assert resp.status_code == 200
        parts = resp.json()
        assert len(parts) == 1
        assert parts[0]["part_category"] == "resistor"

    def test_pending_reviews_endpoint_returns_saved_reviews(self, client):
        c, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })
        save_pending_review(db, part_id, {"manufacturer": "Texas Instruments"}, [{
            "field_name": "manufacturer",
            "field_value": "Texas Instruments",
            "source_tier": "primary_api",
            "source_kind": "api",
            "source_locator": "https://digikey.example/TLV62565DBVR",
            "extraction_method": "api",
            "confidence_marker": "high",
            "conflict_status": "clear",
            "normalization_method": "direct_copy",
            "competing_candidates": [],
        }])

        resp = c.get("/inventory/pending")

        assert resp.status_code == 200
        reviews = resp.json()["reviews"]
        assert str(part_id) in reviews
        assert reviews[str(part_id)]["fields"]["manufacturer"]["value"] == "Texas Instruments"

    def test_part_provenance_endpoint_returns_saved_field_provenance(self, client):
        c, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": None, "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })
        server.update_fields_with_provenance(db, part_id, {"manufacturer": "Texas Instruments"}, [{
            "field_name": "manufacturer",
            "field_value": "Texas Instruments",
            "source_tier": "primary_api",
            "source_kind": "api",
            "source_locator": "https://digikey.example/TLV62565DBVR",
            "extraction_method": "api",
            "confidence_marker": "high",
            "conflict_status": "clear",
            "normalization_method": "direct_copy",
            "competing_candidates": [],
            "evidence": "Manufacturer: Texas Instruments",
        }])

        resp = c.get(f"/inventory/{part_id}/provenance")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["part_id"] == part_id
        assert len(payload["provenance"]) == 1
        assert payload["provenance"][0]["field_name"] == "manufacturer"
        assert payload["provenance"][0]["source_tier"] == "primary_api"

    def test_patch_inventory_updates_part_fields(self, client):
        c, db = client
        part_id = upsert(db, {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })

        resp = c.patch(f"/inventory/{part_id}", json={
            "part": {
                "part_category": "resistor",
                "profile": "passive",
                "value": "22k",
                "package": "0603",
                "quantity": 12,
                "manufacturer": "Yageo",
                "description": "updated",
            }
        })

        assert resp.status_code == 200
        part = resp.json()["part"]
        assert part["value"] == "22k"
        assert part["package"] == "0603"
        assert part["quantity"] == 12
        assert part["manufacturer"] == "Yageo"
        assert part["description"] == "updated"

    def test_patch_inventory_clears_stale_pending_review(self, client):
        c, db = client
        part_id = upsert(db, {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })
        save_pending_review(db, part_id, {"manufacturer": "Yageo"}, [{
            "field_name": "manufacturer",
            "field_value": "Yageo",
            "source_tier": "primary_api",
            "source_kind": "api",
            "source_locator": "https://example.com/part",
            "extraction_method": "api",
            "confidence_marker": "high",
            "conflict_status": "clear",
            "normalization_method": "direct_copy",
            "competing_candidates": [],
        }])

        resp = c.patch(f"/inventory/{part_id}", json={
            "part": {
                "part_category": "resistor",
                "profile": "passive",
                "value": "22k",
                "package": "0603",
                "quantity": 12,
            }
        })

        assert resp.status_code == 200
        assert list_pending_reviews(db) == {}

    def test_patch_inventory_repairs_passive_slot_mixup(self, client):
        c, db = client
        part_id = upsert(db, {
            "part_category": "capacitor", "profile": "passive",
            "value": "100n", "package": "0402", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })

        resp = c.patch(f"/inventory/{part_id}", json={
            "part": {
                "part_category": "capacitor",
                "profile": "discrete_ic",
                "value": "0603",
                "package": "0603",
                "part_number": "1uF",
                "quantity": 20,
            }
        })

        assert resp.status_code == 200
        part = resp.json()["part"]
        assert part["profile"] == "passive"
        assert part["value"] == "1uF"
        assert part["package"] == "0603"
        assert part["part_number"] is None

    def test_patch_inventory_returns_conflict_when_duplicate_identity(self, client):
        c, db = client
        first_id = upsert(db, {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })
        upsert(db, {
            "part_category": "resistor", "profile": "passive",
            "value": "22k", "package": "0603", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })

        resp = c.patch(f"/inventory/{first_id}", json={
            "part": {
                "part_category": "resistor",
                "profile": "passive",
                "value": "22k",
                "package": "0603",
                "quantity": 5,
            }
        })

        assert resp.status_code == 409

    def test_delete_inventory_removes_part(self, client):
        c, db = client
        part_id = upsert(db, {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })

        resp = c.delete(f"/inventory/{part_id}")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert server.get_by_id(db, part_id) is None

    def test_delete_inventory_returns_404_for_missing_part(self, client):
        c, _ = client

        resp = c.delete("/inventory/9999")

        assert resp.status_code == 404


class TestChatValidation:
    def test_no_message_no_photo_returns_422(self, client):
        c, _ = client
        resp = c.post("/chat")
        assert resp.status_code == 422

    def test_photo_without_message_does_not_422(self, client):
        c, _ = client

        async def fake_chat(*args, **kwargs):
            yield 'event: result\ndata: {"type":"chat","response":"ok","action":"none","part":null}\n\n'
            yield 'event: done\ndata: {}\n\n'

        with patch.object(server, "_chat_stream", side_effect=fake_chat):
            resp = c.post(
                "/chat",
                files={"photo": ("part.jpg", _jpeg_bytes(), "image/jpeg")},
            )
        assert resp.status_code == 200

    def test_unsupported_image_type_returns_400(self, client):
        c, _ = client
        resp = c.post(
            "/chat",
            data={"message": "add this"},
            files={"photo": ("part.gif", b"GIF89a", "image/gif")},
        )
        assert resp.status_code == 400


class TestExecuteAction:
    @pytest.mark.asyncio
    async def test_upsert_requires_part_category_and_quantity(self, client):
        _, db = client
        with patch.object(server, "_track_background_task") as track_task:
            part_id, status = await server._execute_action({
                "type": "upsert",
                "part_category": "resistor", "profile": "passive",
                "value": "10k", "package": "0402", "quantity": 5,
                "part_number": None, "description": None, "items": None,
            })
        assert part_id is not None
        assert status == "saved"
        track_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_background_upsert_enrichment_saves_pending_review(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
            "specs": {
                "manufacturer": "Texas Instruments",
                "description": "Buck regulator",
            },
            "chosen_updates": {
                "manufacturer": "Texas Instruments",
                "description": "Buck regulator",
            },
            "provider": "digikey",
            "matched_part_number": "TLV62565DBVR",
            "tried_providers": ["digikey"],
            "status": "saved",
            "outcome": "saved",
            "conflicts": [],
            "durable_provenance": [
                {
                    "field_name": "manufacturer",
                    "field_value": "Texas Instruments",
                    "source_tier": "primary_api",
                    "source_kind": "api",
                    "source_locator": "https://digikey.example/TLV62565DBVR",
                    "extraction_method": "api",
                    "confidence_marker": "high",
                    "conflict_status": "clear",
                    "normalization_method": "direct_copy",
                    "competing_candidates": [],
                },
                {
                    "field_name": "description",
                    "field_value": "Buck regulator",
                    "source_tier": "primary_api",
                    "source_kind": "api",
                    "source_locator": "https://digikey.example/TLV62565DBVR",
                    "extraction_method": "api",
                    "confidence_marker": "high",
                    "conflict_status": "clear",
                    "normalization_method": "direct_copy",
                    "competing_candidates": [],
                },
            ],
        })):
            await server._enrich_upserted_part(part_id, "TLV62565DBVR")

        pending = list_pending_reviews(db)
        assert pending[part_id]["fields"]["manufacturer"]["value"] == "Texas Instruments"
        assert pending[part_id]["fields"]["description"]["value"] == "Buck regulator"

    @pytest.mark.asyncio
    async def test_upsert_without_quantity_returns_none(self, client):
        _, db = client
        part_id, status = await server._execute_action({
            "type": "upsert",
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": None,
            "part_number": None, "description": None, "items": None,
        })
        assert part_id is None
        assert status == "invalid"

    @pytest.mark.asyncio
    async def test_batch_upsert_saves_multiple_records(self, client):
        _, db = client
        with patch.object(server, "_track_background_task") as track_task:
            part_id, status = await server._execute_action({
                "type": "upsert",
                "id": None,
                "items": [
                    {
                        "part_category": "resistor",
                        "profile": "passive",
                        "value": "10k",
                        "package": "0603",
                        "part_number": None,
                        "quantity": 20,
                        "description": "0603 chip resistor 10k",
                    },
                    {
                        "part_category": "resistor",
                        "profile": "passive",
                        "value": "100k",
                        "package": "0603",
                        "part_number": None,
                        "quantity": 20,
                        "description": "0603 chip resistor 100k",
                    },
                ],
                "part_category": None,
                "profile": None,
                "value": None,
                "package": None,
                "part_number": None,
                "quantity": None,
                "description": None,
            })
        assert part_id is not None
        assert status == "saved-batch"
        assert len(server.list_all(db)) == 2
        track_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_repairs_passive_fields_when_package_lands_in_value_slot(self, client):
        _, db = client

        part_id, status = await server._execute_action({
            "type": "upsert",
            "id": None,
            "items": None,
            "part_category": "capacitor",
            "profile": "discrete_ic",
            "value": "0603",
            "package": "0603",
            "part_number": "100nF",
            "quantity": 20,
            "description": "0603 chip capacitor, 100nF value",
        })

        saved = server.get_by_id(db, part_id)
        assert status == "saved"
        assert saved["profile"] == "passive"
        assert saved["value"] == "100n"
        assert saved["package"] == "0603"
        assert saved["part_number"] is None

    @pytest.mark.asyncio
    async def test_batch_upsert_repairs_each_passive_item(self, client):
        _, db = client

        part_id, status = await server._execute_action({
            "type": "upsert",
            "id": None,
            "items": [
                {
                    "part_category": "capacitor",
                    "profile": "discrete_ic",
                    "value": "0603",
                    "package": "0603",
                    "part_number": "10pF",
                    "quantity": 20,
                    "description": "0603 chip capacitor, 10pF value",
                },
                {
                    "part_category": "capacitor",
                    "profile": "discrete_ic",
                    "value": "0603",
                    "package": "0603",
                    "part_number": "100nF",
                    "quantity": 20,
                    "description": "0603 chip capacitor, 100nF value",
                },
            ],
            "part_category": None,
            "profile": None,
            "value": None,
            "package": None,
            "part_number": None,
            "quantity": None,
            "description": None,
        })

        rows = server.list_all(db)
        assert status == "saved-batch"
        assert part_id is not None
        assert len(rows) == 2
        assert {row["value"] for row in rows} == {"10p", "100n"}
        assert all(row["profile"] == "passive" for row in rows)
        assert all(row["part_number"] is None for row in rows)

    @pytest.mark.asyncio
    async def test_batch_update_updates_multiple_records(self, client):
        _, db = client
        first_id = upsert(db, {
            "part_category": "capacitor", "profile": "discrete_ic",
            "value": "0603", "package": "0603", "quantity": 20,
            "part_number": "10PF", "manufacturer": None, "description": "0603 chip capacitor, 10PF value",
        })
        second_id = upsert(db, {
            "part_category": "capacitor", "profile": "discrete_ic",
            "value": "0603", "package": "0603", "quantity": 20,
            "part_number": "100NF", "manufacturer": None, "description": "0603 chip capacitor, 100NF value",
        })

        part_id, status = await server._execute_action({
            "type": "update",
            "id": None,
            "items": [
                {
                    "id": first_id,
                    "part_category": "capacitor",
                    "profile": "passive",
                    "value": "10pF",
                    "package": "0603",
                    "part_number": None,
                    "quantity": 20,
                    "description": "0603 chip capacitor, 10PF value",
                },
                {
                    "id": second_id,
                    "part_category": "capacitor",
                    "profile": "passive",
                    "value": "100nF",
                    "package": "0603",
                    "part_number": None,
                    "quantity": 20,
                    "description": "0603 chip capacitor, 100NF value",
                },
            ],
            "part_category": None,
            "profile": None,
            "value": None,
            "package": None,
            "part_number": None,
            "quantity": None,
            "description": None,
        })

        first = server.get_by_id(db, first_id)
        second = server.get_by_id(db, second_id)
        assert status == "saved-batch"
        assert part_id == second_id
        assert first["value"] == "10pF"
        assert second["value"] == "100nF"
        assert first["part_number"] is None
        assert second["part_number"] is None

    @pytest.mark.asyncio
    async def test_batch_update_updates_quantities(self, client):
        _, db = client
        first_id = upsert(db, {
            "part_category": "capacitor", "profile": "passive",
            "value": "10pF", "package": "0603", "quantity": 60,
            "part_number": None, "manufacturer": None, "description": "0603 chip capacitor, 10pF value",
        })
        second_id = upsert(db, {
            "part_category": "capacitor", "profile": "passive",
            "value": "100nF", "package": "0603", "quantity": 40,
            "part_number": None, "manufacturer": None, "description": "0603 chip capacitor, 100nF value",
        })

        part_id, status = await server._execute_action({
            "type": "update",
            "id": None,
            "items": [
                {
                    "id": first_id,
                    "part_category": "capacitor",
                    "profile": "passive",
                    "value": "10pF",
                    "package": "0603",
                    "part_number": None,
                    "quantity": 20,
                    "description": "0603 chip capacitor, 10pF value",
                },
                {
                    "id": second_id,
                    "part_category": "capacitor",
                    "profile": "passive",
                    "value": "100nF",
                    "package": "0603",
                    "part_number": None,
                    "quantity": 20,
                    "description": "0603 chip capacitor, 100nF value",
                },
            ],
            "part_category": None,
            "profile": None,
            "value": None,
            "package": None,
            "part_number": None,
            "quantity": None,
            "description": None,
        })

        first = server.get_by_id(db, first_id)
        second = server.get_by_id(db, second_id)
        assert status == "saved-batch"
        assert part_id == second_id
        assert first["quantity"] == 20
        assert second["quantity"] == 20

    @pytest.mark.asyncio
    async def test_update_clears_pending_review_for_edited_part(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "manufacturer": None, "description": None,
        })
        save_pending_review(db, part_id, {"manufacturer": "Yageo"}, [{
            "field_name": "manufacturer",
            "field_value": "Yageo",
            "source_tier": "primary_api",
            "source_kind": "api",
            "source_locator": "https://example.com/part",
            "extraction_method": "api",
            "confidence_marker": "high",
            "conflict_status": "clear",
            "normalization_method": "direct_copy",
            "competing_candidates": [],
        }])

        saved_id, status = await server._execute_action({
            "type": "update",
            "id": part_id,
            "items": None,
            "part_category": "resistor",
            "profile": "passive",
            "value": "22k",
            "package": "0603",
            "part_number": None,
            "quantity": None,
            "description": "updated",
        })

        assert status == "saved"
        assert saved_id == part_id
        assert list_pending_reviews(db) == {}

    @pytest.mark.asyncio
    async def test_action_none_returns_none(self, client):
        _, db = client
        assert await server._execute_action({"type": "none"}) == (None, "noop")

    @pytest.mark.asyncio
    async def test_lookup_without_specs_reports_no_specs(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
            "specs": {},
            "chosen_updates": {},
            "provider": None,
            "matched_part_number": None,
            "tried_providers": ["digikey"],
            "status": "no_match",
            "outcome": "no_match",
            "durable_provenance": [],
        })):
            result = await server._execute_action({
                "type": "lookup",
                "id": part_id,
                "part_number": "TLV62565DBVR",
            })

        assert result == (part_id, "no-specs")

    @pytest.mark.asyncio
    async def test_lookup_timeout_reports_timeout_status(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
            "specs": {},
            "chosen_updates": {},
            "provider": None,
            "matched_part_number": None,
            "tried_providers": ["digikey"],
            "outcome": "timeout",
            "status": "timeout",
            "durable_provenance": [],
        })):
            result = await server._execute_action({
                "type": "lookup",
                "id": part_id,
                "part_number": "TLV62565DBVR",
            })

        assert result == (part_id, "lookup-timeout")

    @pytest.mark.asyncio
    async def test_lookup_incomplete_reports_incomplete_status(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
            "specs": {},
            "chosen_updates": {},
            "provider": None,
            "matched_part_number": None,
            "tried_providers": ["digikey"],
            "outcome": "incomplete",
            "status": "incomplete",
            "durable_provenance": [],
            "conflicts": [],
        })):
            result = await server._execute_action({
                "type": "lookup",
                "id": part_id,
                "part_number": "TLV62565DBVR",
            })

        assert result == (part_id, "lookup-incomplete")

    @pytest.mark.asyncio
    async def test_lookup_failed_reports_failed_status(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
            "specs": {},
            "chosen_updates": {},
            "provider": None,
            "matched_part_number": None,
            "tried_providers": ["digikey"],
            "outcome": "failed",
            "status": "failed",
            "durable_provenance": [],
            "conflicts": [],
        })):
            result = await server._execute_action({
                "type": "lookup",
                "id": part_id,
                "part_number": "TLV62565DBVR",
            })

        assert result == (part_id, "lookup-failed")


class TestChatStream:
    @pytest.mark.asyncio
    async def test_lookup_no_specs_overrides_misleading_model_text(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "_llm") as llm:
            llm.chat = AsyncMock(return_value={
                "response": "I've fetched the detailed specifications for the TLV62565DBVR.",
                "db_action": {
                    "type": "lookup",
                    "id": part_id,
                    "part_category": None,
                    "profile": None,
                    "value": None,
                    "package": None,
                    "part_number": "TLV62565DBVR",
                    "quantity": None,
                    "description": None,
                },
            })
            with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
                "specs": {},
                "chosen_updates": {},
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["digikey"],
                "status": "no_match",
                "outcome": "no_match",
                "durable_provenance": [],
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "did not return matching specifications" in events[0]

    @pytest.mark.asyncio
    async def test_lookup_saved_overrides_misleading_model_text(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "_llm") as llm:
            llm.chat = AsyncMock(return_value={
                "response": "I've fetched the detailed specifications for the TLV62565DBVR.",
                "db_action": {
                    "type": "lookup",
                    "id": part_id,
                    "part_category": None,
                    "profile": None,
                    "value": None,
                    "package": None,
                    "part_number": "TLV62565DBVR",
                    "quantity": None,
                    "description": "Buck Switching Regulator IC Positive Adjustable 0.6V 1 Output 1.5A SC-74A, SOT-753",
                },
            })
            with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
                "specs": {
                    "part_number": "TLV62565DBVR",
                    "manufacturer": "Texas Instruments",
                    "description": "Buck Switching Regulator IC Positive Adjustable 0.6V 1 Output 1.5A SC-74A, SOT-753",
                },
                "chosen_updates": {
                    "part_number": "TLV62565DBVR",
                    "manufacturer": "Texas Instruments",
                    "description": "Buck Switching Regulator IC Positive Adjustable 0.6V 1 Output 1.5A SC-74A, SOT-753",
                },
                "provider": "digikey",
                "matched_part_number": "TLV62565DBVR",
                "tried_providers": ["digikey"],
                "status": "saved",
                "outcome": "saved",
                "durable_provenance": [
                    {
                        "field_name": "manufacturer",
                        "field_value": "Texas Instruments",
                        "source_tier": "primary_api",
                        "source_kind": "api",
                        "source_locator": "https://digikey.example/TLV62565DBVR",
                        "extraction_method": "api",
                        "confidence_marker": "high",
                        "conflict_status": "clear",
                        "normalization_method": "direct_copy",
                        "competing_candidates": [],
                    },
                    {
                        "field_name": "description",
                        "field_value": "Buck Switching Regulator IC Positive Adjustable 0.6V 1 Output 1.5A SC-74A, SOT-753",
                        "source_tier": "primary_api",
                        "source_kind": "api",
                        "source_locator": "https://digikey.example/TLV62565DBVR",
                        "extraction_method": "api",
                        "confidence_marker": "high",
                        "conflict_status": "clear",
                        "normalization_method": "direct_copy",
                        "competing_candidates": [],
                    },
                ],
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "I updated the inventory record with the fetched specifications." in events[0]
        assert "Buck Switching Regulator IC" in events[0]
        provenance = list_field_provenance(db, part_id)
        assert {row["field_name"] for row in provenance} == {"description", "manufacturer"}

    @pytest.mark.asyncio
    async def test_lookup_timeout_overrides_misleading_model_text(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": "Texas Instruments",
            "description": "Wrong stale description",
        })

        with patch.object(server, "_llm") as llm:
            llm.chat = AsyncMock(return_value={
                "response": "Great! I'm fetching the detailed specifications for the TLV62565DBVR from DigiKey now.",
                "db_action": {
                    "type": "lookup",
                    "id": part_id,
                    "part_category": None,
                    "profile": None,
                    "value": None,
                    "package": None,
                    "part_number": "TLV62565DBVR",
                    "quantity": None,
                    "description": None,
                },
            })
            with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
                "specs": {},
                "chosen_updates": {},
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["digikey"],
                "outcome": "timeout",
                "status": "timeout",
                "durable_provenance": [],
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "timed out" in events[0]
        assert "Wrong stale description" not in events[0]

    @pytest.mark.asyncio
    async def test_lookup_conflict_overrides_misleading_model_text(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "_llm") as llm:
            llm.chat = AsyncMock(return_value={
                "response": "I updated the metadata from the provider results.",
                "db_action": {
                    "type": "lookup",
                    "id": part_id,
                    "part_category": None,
                    "profile": None,
                    "value": None,
                    "package": None,
                    "part_number": "TLV62565DBVR",
                    "quantity": None,
                    "description": None,
                },
            })
            with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
                "specs": {},
                "chosen_updates": {},
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["digikey"],
                "outcome": "conflict",
                "status": "conflict",
                "durable_provenance": [],
                "conflicts": [{"field_name": "manufacturer"}],
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "conflicting high-authority part metadata" in events[0]

    @pytest.mark.asyncio
    async def test_lookup_incomplete_overrides_misleading_model_text(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "_llm") as llm:
            llm.chat = AsyncMock(return_value={
                "response": "I updated the metadata from the provider results.",
                "db_action": {
                    "type": "lookup",
                    "id": part_id,
                    "part_category": None,
                    "profile": None,
                    "value": None,
                    "package": None,
                    "part_number": "TLV62565DBVR",
                    "quantity": None,
                    "description": None,
                },
            })
            with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
                "specs": {},
                "chosen_updates": {},
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["digikey"],
                "outcome": "incomplete",
                "status": "incomplete",
                "durable_provenance": [],
                "conflicts": [],
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "still did not expose enough trustworthy metadata" in events[0]

    @pytest.mark.asyncio
    async def test_lookup_failed_overrides_misleading_model_text(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "_llm") as llm:
            llm.chat = AsyncMock(return_value={
                "response": "I updated the metadata from the provider results.",
                "db_action": {
                    "type": "lookup",
                    "id": part_id,
                    "part_category": None,
                    "profile": None,
                    "value": None,
                    "package": None,
                    "part_number": "TLV62565DBVR",
                    "quantity": None,
                    "description": None,
                },
            })
            with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
                "specs": {},
                "chosen_updates": {},
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["digikey"],
                "outcome": "failed",
                "status": "failed",
                "durable_provenance": [],
                "conflicts": [],
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "terminated due to a provider or retrieval error" in events[0]

    @pytest.mark.asyncio
    async def test_lookup_needs_confirmation_overrides_misleading_model_text(self, client):
        _, db = client
        part_id = upsert(db, {
            "part_category": "discrete_ic", "profile": "discrete_ic",
            "value": "TLV62565DBVR", "package": "SOT-23-5", "quantity": 10,
            "part_number": "TLV62565DBVR", "manufacturer": None, "description": None,
        })

        with patch.object(server, "_llm") as llm:
            llm.chat = AsyncMock(return_value={
                "response": "I updated the metadata from the provider results.",
                "db_action": {
                    "type": "lookup",
                    "id": part_id,
                    "part_category": None,
                    "profile": None,
                    "value": None,
                    "package": None,
                    "part_number": "TLV62565DBVR",
                    "quantity": None,
                    "description": None,
                },
            })
            with patch.object(server, "fetch_specs_detailed", AsyncMock(return_value={
                "specs": {},
                "chosen_updates": {},
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["digikey"],
                "outcome": "needs_confirmation",
                "status": "needs_confirmation",
                "durable_provenance": [],
                "conflicts": [],
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "requires explicit confirmation" in events[0]
