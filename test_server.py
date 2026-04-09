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
        part_id = await server._execute_action("upsert", {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": 5,
            "part_number": None, "description": None,
        })
        assert part_id is not None

    @pytest.mark.asyncio
    async def test_upsert_without_quantity_returns_none(self, client):
        _, db = client
        part_id = await server._execute_action("upsert", {
            "part_category": "resistor", "profile": "passive",
            "value": "10k", "package": "0402", "quantity": None,
            "part_number": None, "description": None,
        })
        assert part_id is None

    @pytest.mark.asyncio
    async def test_action_none_returns_none(self, client):
        _, db = client
        assert await server._execute_action("none", {}) is None
