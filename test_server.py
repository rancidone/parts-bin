"""Tests for server HTTP endpoints."""

import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from starlette.testclient import TestClient

import server
from db.persistence import init_db, upsert


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
        part_id, status = await server._execute_action("upsert", {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "description": None,
        })
        assert part_id is not None
        assert status == "saved"

    @pytest.mark.asyncio
    async def test_upsert_without_quantity_returns_none(self, client):
        _, db = client
        part_id, status = await server._execute_action("upsert", {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": None,
            "part_number": None, "description": None,
        })
        assert part_id is None
        assert status == "invalid"

    @pytest.mark.asyncio
    async def test_action_none_returns_none(self, client):
        _, db = client
        assert await server._execute_action("none", {}) == (None, "noop")

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
            "provider": None,
            "matched_part_number": None,
            "tried_providers": ["lcsc", "digikey"],
            "status": "no-match",
        })):
            result = await server._execute_action("lookup", {
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
            "provider": None,
            "matched_part_number": None,
            "tried_providers": ["lcsc", "digikey"],
            "status": "timeout",
        })):
            result = await server._execute_action("lookup", {
                "id": part_id,
                "part_number": "TLV62565DBVR",
            })

        assert result == (part_id, "lookup-timeout")


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
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["lcsc", "digikey"],
                "status": "no-match",
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
                    "manufacturer": "Texas Instruments",
                    "description": "Buck Switching Regulator IC Positive Adjustable 0.6V 1 Output 1.5A SC-74A, SOT-753",
                },
                "provider": "digikey",
                "matched_part_number": "TLV62565DBVR",
                "tried_providers": ["lcsc", "digikey"],
                "status": "ok",
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "I updated the inventory record with the fetched specifications." in events[0]
        assert "Buck Switching Regulator IC" in events[0]

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
                "provider": None,
                "matched_part_number": None,
                "tried_providers": ["lcsc", "digikey"],
                "status": "timeout",
            })):
                events = [event async for event in server._chat_stream("Yes, fetch more info", None)]

        assert "timed out" in events[0]
        assert "Wrong stale description" not in events[0]
