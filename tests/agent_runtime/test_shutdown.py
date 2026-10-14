"""A real HTTP host can exit while accepted work continues in another process."""

import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

import httpx

from tests.storage import TestRepository


async def test_http_shutdown_does_not_cancel_durable_worker(tmp_path):
    module = tmp_path / 'shutdown_app.py'
    module.write_text('''
import asyncio
import os
from agent_runtime import ModelTurn, ToolCall
from application import create_services
from tests.storage import TestRepository
from server import create_app

class Transport:
    async def start(self, request):
        return 'resp_first' if not request.exchanges else 'resp_final'
    async def retrieve(self, response_id):
        if response_id == 'resp_first':
            return ModelTurn(tool_calls=(ToolCall('add_stock', {'part_id': 1, 'quantity': 2}, 'add'),))
        await asyncio.sleep(1)
        return ModelTurn('Added once')

def services():
    repository = TestRepository(os.environ['SHUTDOWN_TEST_DATABASE'])
    return create_services(repository, repository.conversations, transport_factory=Transport)

def factory():
    return create_app(services())
''')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    database = tmp_path / 'installation'
    repository = TestRepository(database)
    from domain import AddPartRequest, PartFields, PartsBinService
    PartsBinService(repository).add_part(AddPartRequest(PartFields('resistor', 'passive', 1, value='10k')))
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(tmp_path), str(Path.cwd()))),
                   'SHUTDOWN_TEST_DATABASE': str(database)}
    worker_code = '''
import asyncio
from shutdown_app import services
import db.postgres.dispatch as persistence
persistence.POLL_SECONDS = 0
async def work():
    app = services()
    try:
        for _ in range(500):
            app.dispatch.reconcile()
            await app.dispatch.run_once()
            await asyncio.sleep(.02)
    finally:
        await app.close()
asyncio.run(work())
'''
    with (tmp_path / 'server.log').open('w+') as output:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'shutdown_app:factory', '--factory',
            '--host', '127.0.0.1', '--port', str(port), '--timeout-graceful-shutdown', '1'],
            env=environment, stdout=output, stderr=output)
        worker = subprocess.Popen([sys.executable, '-c', worker_code], env=environment, stdout=output, stderr=output)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=10) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        output.seek(0)
                        raise AssertionError(output.read()[-3000:])
                    try:
                        if (await client.get('/health')).status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    await asyncio.sleep(.05)
                thread = (await client.post('/agent/threads')).json()['thread_id']
                async with client.stream('POST', f'/agent/threads/{thread}/messages',
                                         data={'message': 'Add two', 'execution_id': 'request'}) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith('data: ') and json.loads(line[6:])['kind'] == 'tool_result':
                            process.send_signal(signal.SIGTERM)
                            break
                await asyncio.to_thread(process.wait, timeout=5)
            assert process.returncode in (0, -signal.SIGTERM)
            for _ in range(100):
                events = repository.conversations.events(thread)
                if events[-1].kind == 'completed':
                    break
                await asyncio.sleep(.05)
            assert events[-1].data['status'] == 'completed'
            assert PartsBinService(repository).list()[0].quantity == 3
            assert sum(event.kind == 'tool_result' for event in events) == 1
        finally:
            for child in (process, worker):
                if child.poll() is None:
                    child.terminate()
                    try:
                        await asyncio.to_thread(child.wait, timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        await asyncio.to_thread(child.wait, timeout=5)
