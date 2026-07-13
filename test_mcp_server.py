"""Tests for the MCP tool surface mounted at /mcp."""

import socket
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import server
from db.persistence import init_db

_TEST_TOKEN = "test-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _running_server():
    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("server did not start in time")
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def _server_base_url():
    # FastMCP's StreamableHTTPSessionManager can only be .run() once per
    # instance, and _mcp is a module-level singleton in server.py — so the
    # server process is started once for the whole test module, not per test.
    with patch.object(server, "_MCP_API_KEY", _TEST_TOKEN):
        with _running_server() as base_url:
            yield base_url


@pytest.fixture
def mcp_base_url(tmp_path, _server_base_url):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with patch.object(server, "_DB_PATH", db_path):
        yield f"{_server_base_url}/mcp/"


async def _call_tool(mcp_url: str, name: str, arguments: dict | None = None):
    headers = {"Authorization": f"Bearer {_TEST_TOKEN}"}
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(name, arguments or {})


def _result_text(result) -> str:
    return result.content[0].text


@pytest.mark.asyncio
async def test_missing_token_rejected(mcp_base_url):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            mcp_base_url,
            json={},
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_rejected(mcp_base_url):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            mcp_base_url,
            json={},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": "Bearer nope",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bare_mcp_path_redirects(mcp_base_url):
    bare_url = mcp_base_url.rstrip("/")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            bare_url,
            json={},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_TEST_TOKEN}",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 307
    assert resp.headers["location"] == "/mcp/"


@pytest.mark.asyncio
async def test_add_search_get_update_delete_round_trip(mcp_base_url):
    added = await _call_tool(
        mcp_base_url,
        "add_part",
        {
            "part_category": "resistor",
            "profile": "passive",
            "quantity": 10,
            "value": "10k",
            "package": "0805",
        },
    )
    assert not added.isError
    assert '"quantity": 10' in _result_text(added)

    found = await _call_tool(mcp_base_url, "search_parts", {"part_category": "resistor"})
    assert not found.isError
    assert found.structuredContent["result"][0]["value"] == "10k"
    part_id = found.structuredContent["result"][0]["id"]

    got = await _call_tool(mcp_base_url, "get_part", {"part_id": part_id})
    assert not got.isError
    assert '"package": "0805"' in _result_text(got)

    updated = await _call_tool(mcp_base_url, "update_part", {"part_id": part_id, "quantity": 25})
    assert not updated.isError
    assert '"quantity": 25' in _result_text(updated)

    deleted = await _call_tool(mcp_base_url, "delete_part", {"part_id": part_id})
    assert not deleted.isError

    missing = await _call_tool(mcp_base_url, "get_part", {"part_id": part_id})
    assert missing.isError


@pytest.mark.asyncio
async def test_add_part_duplicate_increments_quantity(mcp_base_url):
    part = {
        "part_category": "resistor",
        "profile": "passive",
        "quantity": 5,
        "value": "1k",
        "package": "0603",
    }
    first = await _call_tool(mcp_base_url, "add_part", part)
    second = await _call_tool(mcp_base_url, "add_part", part)
    assert not first.isError
    assert not second.isError
    assert '"quantity": 10' in _result_text(second)

    found = await _call_tool(mcp_base_url, "search_parts", {})
    assert len(found.structuredContent["result"]) == 1
