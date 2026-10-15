"""Assembly, host independence, and per-installation lifecycle behavior."""

import os
import subprocess
import sys
from pathlib import Path

from starlette.testclient import TestClient

from agent_runtime import ModelTurn, ToolCall
from application import create_services
from tests.storage import TestConversations
from tests.storage import TestRepository
from domain import AddPartRequest, PartFields
from server import create_app


class Transport:
    def __init__(self):
        self.calls = 0
        self.closed = False

    async def start(self, request):
        self.request = request
        return 'resp_test'

    async def retrieve(self, response_id):
        return await self.complete(self.request)

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
    repository = TestRepository(directory / "inventory.db")
    conversations = repository.conversations
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
        import threading
        import time
        stopping = threading.Event()
        def worker():
            while not stopping.is_set():
                with second.domain.repository.transaction() as unit:
                    unit.connection.execute('UPDATE agent_dispatch SET available_at = 0 WHERE settled = 0')
                two.portal.call(second.dispatch.run_once)
                time.sleep(0.01)
        task = threading.Thread(target=worker)
        task.start()
        try:
            response = two.post(f"/agent/threads/{thread}/messages", data={"message": "Add two 10k resistors", "execution_id": "test-request"})
        finally:
            stopping.set()
            task.join(timeout=5)
        assert '"status": "completed"' in response.text
        assert one.get("/inventory").json()[0]["quantity"] == 1
        assert two.get("/inventory").json()[0]["quantity"] == 2
    assert second_transport.closed
    assert first_transport.calls == 0


def test_local_factory_loads_explicit_configuration_at_construction(tmp_path, monkeypatch):
    import local_app

    repository = TestRepository(tmp_path / 'installation')
    config = tmp_path / "local.toml"
    config.write_text('[db]\nhost = "localhost"\ndatabase = "parts_bin"\nuser = "parts_bin"\n')
    monkeypatch.setenv("PARTS_BIN_CONFIG", str(config))
    monkeypatch.setattr(local_app.log, "init", lambda: None)
    def build(supplied):
        assert supplied.database == 'parts_bin'
        assert supplied.user == 'parts_bin'
        return repository
    monkeypatch.setattr(local_app, 'PostgresPartsBinRepository', build)
    app = local_app.create_app()
    with TestClient(app) as client:
        assert client.get("/health").json()["agent_configured"] is False
        thread = client.post("/agent/threads").json()["thread_id"]
        result = client.post(f"/agent/threads/{thread}/messages", data={"message": "hello", "execution_id": "test-request"})
        assert result.status_code == 503


async def test_worker_steps_reassemble_services_without_http(tmp_path):
    inventory, events = tmp_path / "inventory.db", tmp_path / "events.db"
    first_transport = Transport()

    def assemble(transport):
        return create_services(TestRepository(inventory),
                               TestConversations(events),
                               transport_factory=lambda: transport)

    first = assemble(first_transport)
    thread = first.gateway.create_thread()
    result = await first.gateway.advance(thread, "Add two 10k resistors")
    execution_id = result.execution_id
    assert result.status == "ready"
    assert first.domain.list() == []
    await first.close()
    assert first_transport.closed

    second_transport = Transport()
    second = assemble(second_transport)
    result = await second.gateway.advance(thread, execution_id=execution_id)
    assert result.status == "ready"
    assert second.domain.list()[0].quantity == 2
    assert second_transport.calls == 0
    await second.close()

    final_transport = Transport()
    final_transport.calls = 1  # The final worker only supplies the acknowledgement.
    final = assemble(final_transport)
    result = await final.gateway.advance(thread, execution_id=execution_id)
    assert result.status == "completed"
    assert final.domain.list()[0].quantity == 2
    assert final.gateway.events(thread) == result.events
    await final.close()


def test_local_configuration_defaults_to_live_and_accepts_explicit_async(tmp_path, monkeypatch):
    import local_app
    config = tmp_path / 'config.toml'
    monkeypatch.setenv('PARTS_BIN_CONFIG', str(config))
    repository = TestRepository(tmp_path / 'configured')
    monkeypatch.setattr(local_app, 'PostgresPartsBinRepository', lambda _config: repository)
    for setting, expected in [('', False), ('background_responses = true', True), ('background_responses = false', False)]:
        config.write_text('[agent]\n' + setting + '\n[db]\nhost="test"\ndatabase="test"\nuser="test"\n')
        services, _ = local_app.create_local_services()
        assert services.background_responses is expected
        assert services.dispatch.background_responses is expected
