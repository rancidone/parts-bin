"""HTTP adapter tests for the gateway and inventory surfaces."""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import server
from db.persistence import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "parts.db"
    init_db(db_path)
    with patch.object(server, "_DB_PATH", db_path):
        with TestClient(server.app, raise_server_exceptions=True) as test_client:
            yield test_client, db_path


def test_health_reports_explicit_runtimes(client):
    response = client[0].get("/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "runtimes"}


@pytest.mark.parametrize("runtime", ["codex", "openai", "local"])
def test_agent_thread_selects_runtime(client, runtime):
    response = client[0].post("/agent/threads", json={"runtime": runtime})
    assert response.status_code == 200
    assert response.json()["runtime"] == runtime


def test_invalid_agent_thread_runtime_is_rejected(client):
    response = client[0].post("/agent/threads", json={"runtime": "unsupported"})
    assert response.status_code == 422


def test_inventory_still_opens(client):
    response = client[0].get("/inventory")
    assert response.status_code == 200
    assert response.json() == []
