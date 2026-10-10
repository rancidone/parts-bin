"""Assembly, host independence, and per-installation lifecycle behavior."""

import os
import subprocess
import sys
from pathlib import Path

from starlette.testclient import TestClient

from agent_runtime import ModelTurn, ToolCall
from application import create_services
from db.conversations import SQLiteConversationRepository
from db.repository import SQLitePartsBinRepository
from domain import AddPartRequest, PartFields
from server import create_app


class Transport:
    def __init__(self):
        self.calls = 0
        self.closed = False

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(tool_calls=(ToolCall("add_part", {
                "part_category": "resistor", "profile": "passive", "quantity": 2, "value": "10k"}, "add"),))
        return ModelTurn("Added")

    async def close(self):
        self.closed = True


def services_at(directory, transport):
    directory.mkdir()
    repository = SQLitePartsBinRepository(directory / "inventory.db")
    conversations = SQLiteConversationRepository(directory / "events.db")
    return create_services(repository, conversations, transport_factory=lambda: transport)


def test_importing_http_and_local_factory_needs_no_config_or_runtime_files(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                   "PARTS_BIN_CONFIG": str(tmp_path / "missing.toml")}
    completed = subprocess.run([sys.executable, "-c", "import server; import local_app"],
                               cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.iterdir()) == []


async def test_worker_assembly_runs_without_http_and_replays_execution(tmp_path):
    transport = Transport()
    services = services_at(tmp_path / "worker", transport)
    thread = services.gateway.create_thread()
    events = await services.gateway.submit(thread, "Add two 10k resistors")
    assert services.domain.list()[0].quantity == 2
    replay = await services.gateway.resume(thread, events[0].data["execution_id"])
    assert replay == events
    assert transport.calls == 2
    await services.close()
    assert transport.closed


def test_http_apps_keep_installations_isolated_and_close_used_transports(tmp_path):
    first_transport, second_transport = Transport(), Transport()
    first = services_at(tmp_path / "first", first_transport)
    second = services_at(tmp_path / "second", second_transport)
    first.domain.add_part(AddPartRequest(PartFields("resistor", "passive", 1, value="10k")))
    with TestClient(create_app(first)) as one, TestClient(create_app(second)) as two:
        assert len(one.get("/inventory").json()) == 1
        assert two.get("/inventory").json() == []
        thread = two.post("/agent/threads").json()["thread_id"]
        assert one.get(f"/agent/threads/{thread}/events").status_code == 404
        response = two.post(f"/agent/threads/{thread}/messages", data={"message": "Add two 10k resistors"})
        assert '"status": "completed"' in response.text
        assert one.get("/inventory").json()[0]["quantity"] == 1
        assert two.get("/inventory").json()[0]["quantity"] == 2
    assert second_transport.closed
    assert first_transport.calls == 0


def test_local_factory_loads_explicit_configuration_at_construction(tmp_path, monkeypatch):
    import local_app

    inventory = tmp_path / "inventory.db"
    conversations = tmp_path / "conversations.db"
    config = tmp_path / "local.toml"
    config.write_text(f'[db]\npath = "{inventory}"\n[agent]\nconversation_db_path = "{conversations}"\n')
    monkeypatch.setenv("PARTS_BIN_CONFIG", str(config))
    monkeypatch.setattr(local_app.log, "init", lambda: None)
    assert not inventory.exists()
    app = local_app.create_app()
    assert inventory.is_file() and conversations.is_file()
    with TestClient(app) as client:
        assert client.get("/health").json()["agent_configured"] is False
        thread = client.post("/agent/threads").json()["thread_id"]
        result = client.post(f"/agent/threads/{thread}/messages", data={"message": "hello"})
        assert "OpenAI API is not configured" in result.text
