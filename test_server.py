"""Tests for server routing logic and HTTP endpoints."""

import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from starlette.testclient import TestClient

import server
from db.persistence import init_db, upsert
from server import _is_ingestion


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
        mock_stream = AsyncMock(return_value=iter([]))

        async def fake_ingest(*args, **kwargs):
            yield '{"type":"done"}'

        with patch.object(server, "_ingestion_stream", side_effect=fake_ingest):
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


class TestRoutingHeuristic:
    def test_photo_always_ingestion(self):
        assert _is_ingestion("what is this?", has_photo=True)

    def test_add_keyword(self):
        assert _is_ingestion("add 10 resistors", has_photo=False)

    def test_i_have_keyword(self):
        assert _is_ingestion("I have 5 2N7002", has_photo=False)

    def test_query_goes_to_query_path(self):
        assert not _is_ingestion("do I have any 10k resistors?", has_photo=False)

    def test_how_many_is_query(self):
        assert not _is_ingestion("how many 100nF caps do I have?", has_photo=False)

    def test_stock_keyword(self):
        assert _is_ingestion("stock 20 0402 caps", has_photo=False)
