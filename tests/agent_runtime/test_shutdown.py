"""Exercise SIGTERM against the real HTTP host with isolated stock and no provider."""

import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

import httpx
import pytest

from agent_runtime import ModelTurn
from tests.storage import TestConversations
from tests.storage import TestRepository
from domain import PartsBinService
from application import create_services


@pytest.mark.parametrize('forced', [False, True])
async def test_shutdown_finishes_or_releases_request_without_repeating_stock(tmp_path, forced):
    module = tmp_path / 'shutdown_app.py'
    module.write_text('''
import asyncio
import os
from agent_runtime import ModelTurn, ToolCall
from application import create_services
from tests.storage import TestConversations
from tests.storage import TestRepository
from server import create_app

class Transport:
    async def complete(self, request):
        if not request.exchanges:
            return ModelTurn(tool_calls=(ToolCall('add_part', {
                'part_category': 'resistor', 'profile': 'passive',
                'value': '10k', 'quantity': 3}, 'add'),))
        await asyncio.sleep(float(os.environ['SHUTDOWN_TEST_DELAY']))
        return ModelTurn('Added once')

def factory():
    database = os.environ['SHUTDOWN_TEST_DATABASE']
    return create_app(create_services(TestRepository(database),
        TestConversations(database), transport_factory=Transport))
''')
    # Reserve a port briefly; all data and requests are disposable test fixtures.
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    database = tmp_path / 'parts.db'
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(tmp_path), str(Path.cwd()))),
                   'SHUTDOWN_TEST_DATABASE': str(database), 'SHUTDOWN_TEST_DELAY': '60' if forced else '0.5'}
    with (tmp_path / 'server.log').open('w+') as output:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'shutdown_app:factory', '--factory',
            '--host', '127.0.0.1', '--port', str(port), '--timeout-graceful-shutdown', '1'],
            env=environment, stdout=output, stderr=output)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=10) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        output.seek(0)
                        pytest.fail(output.read()[-3000:])
                    try:
                        if (await client.get('/health')).status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail('Test HTTP host did not become ready')
                thread = (await client.post('/agent/threads')).json()['thread_id']
                events = []
                async with client.stream('POST', f'/agent/threads/{thread}/messages',
                                         data={'message': 'Add three resistors'}) as response:
                    response.raise_for_status()
                    try:
                        async for line in response.aiter_lines():
                            if line.startswith('data: '):
                                event = json.loads(line[6:])
                                events.append(event)
                                if event['kind'] == 'tool_result':
                                    process.send_signal(signal.SIGTERM)
                    except httpx.RemoteProtocolError:
                        assert forced
                await asyncio.to_thread(process.wait, timeout=5)
            # Uvicorn restores and re-raises the signal after completing shutdown.
            assert process.returncode in (0, -signal.SIGTERM)
            store = TestConversations(database)
            execution = store.events(thread)[0].data['execution_id']
            repository = TestRepository(database)
            assert PartsBinService(repository).list()[0].quantity == 3
            if forced:
                # Cancellation must free ownership immediately, before the 5-minute lease expires.
                record = repository.executions.claim(thread, execution, 'probe')
                assert record.status == 'ready'
                repository.executions.release(execution, 'probe')

                class FinishTransport:
                    async def complete(self, request):
                        return ModelTurn('Recovered acknowledgement')

                services = create_services(repository, store, transport_factory=FinishTransport)
                resumed = await services.gateway.resume(thread, execution)
                assert resumed[-1].data['status'] == 'completed'
                assert PartsBinService(repository).list()[0].quantity == 3
                assert sum(event.kind == 'tool_result' for event in store.events(thread)) == 1
            else:
                assert events[-1]['data']['status'] == 'completed'
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
