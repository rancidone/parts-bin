"""External MCP clients read shared inventory without mutation or model calls."""

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from application import create_services
from domain import AddPartRequest, PartFields
from evaluation.runner import _snapshot, MUTATING_TOOLS
from server import create_app
from tests.storage import TestRepository
from tools.inventory_mcp import MCPConfig, READ_TOOLS
from tools import PartsBinToolRegistry

TOKEN = 'dedicated-test-token-with-32-characters'
HEADERS = {'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/json, text/event-stream',
           'Content-Type': 'application/json', 'MCP-Protocol-Version': '2025-06-18'}


def installation(tmp_path):
    repository = TestRepository(tmp_path / 'inventory')
    def forbidden():
        pytest.fail('MCP must never construct a model transport')
    services = create_services(repository, repository.conversations, transport_factory=forbidden)
    services.domain.add_part(AddPartRequest(PartFields.from_mapping({
        'part_category': 'resistor', 'profile': 'passive', 'value': '10k',
        'package': '0603', 'quantity': 3})))
    return services


@pytest.fixture
def connected(tmp_path):
    services = installation(tmp_path)
    with TestClient(create_app(services, mcp_config=MCPConfig(TOKEN, 'https://testserver'))) as client:
        yield client, services


def rpc(client, method, params=None, **kwargs):
    return client.post('/mcp', headers=HEADERS, json={
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}, **kwargs)


def test_protocol_discovery_preserves_shared_contract_and_marks_reads(connected):
    client, services = connected
    result = rpc(client, 'initialize', {'protocolVersion': '2025-06-18',
        'capabilities': {}, 'clientInfo': {'name': 'external-agent', 'version': '1'}})
    assert result.status_code == 200
    assert result.json()['result']['serverInfo']['name'] == 'parts-bin'
    assert 'Read-only' in result.json()['result']['instructions']
    discovered = rpc(client, 'tools/list').json()['result']['tools']
    assert {tool['name'] for tool in discovered} == READ_TOOLS
    shared = {tool['name']: tool for tool in PartsBinToolRegistry(services.domain).list_tools()}
    for tool in discovered:
        assert tool['inputSchema'] == shared[tool['name']]['inputSchema']
        assert tool['annotations']['readOnlyHint']
        assert not tool['annotations']['destructiveHint']
    assert result.headers.get('mcp-session-id') is None


def test_search_and_bom_use_domain_units_and_keep_stock(connected):
    client, services = connected
    before = _snapshot(services.domain.repository)
    response = rpc(client, 'tools/call', {'name': 'search_parts', 'arguments': {
        'filters': {'part_category': 'resistor', 'value': '10000r'}, 'minimum_quantity': 2}})
    result = response.json()['result']
    assert not result['isError']
    assert result['structuredContent']['result']['parts'][0]['quantity'] == 3
    bom = rpc(client, 'tools/call', {'name': 'check_inventory', 'arguments': {'items': [
        {'filters': {'part_category': 'resistor', 'value': '10 kΩ'}, 'quantity': 4}]}}).json()['result']
    assert bom['structuredContent']['result']['items'][0]['status'] == 'insufficient'
    assert _snapshot(services.domain.repository) == before
    assert not services.gateway.store.threads()


@pytest.mark.parametrize('name', sorted(MUTATING_TOOLS | {'raw_sql', 'not_a_tool'}))
def test_unadvertised_mutation_cannot_be_invoked(connected, name):
    client, services = connected
    before = _snapshot(services.domain.repository)
    result = rpc(client, 'tools/call', {'name': name, 'arguments': {
        'part_id': 1, 'quantity': 99, '_meta': {'approved': True}}}).json()['result']
    assert result['isError']
    assert result['structuredContent']['error']['code'] == 'invalid_input'
    assert _snapshot(services.domain.repository) == before


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
@pytest.mark.parametrize('authorization', ['', 'Bearer wrong', 'Basic secret', 'Bearer é'])
def test_all_transport_methods_require_token(connected, method, authorization):
    client, _ = connected
    headers = {**HEADERS, 'Authorization': authorization.encode('utf-8')}
    response = client.request(method, '/mcp', headers=headers)
    assert response.status_code == 401
    assert response.headers['www-authenticate'].startswith('Bearer')
    assert TOKEN not in response.text


def test_host_origin_and_body_limits(connected):
    client, _ = connected
    message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    for headers, status in [({'Host': 'attacker.example'}, 421), ({'Origin': 'https://attacker.example'}, 403)]:
        response = client.post('/mcp', headers={**HEADERS, **headers}, json=message)
        assert response.status_code == status
    response = client.post('/mcp', headers=HEADERS, content='x' * (128 * 1024 + 1))
    assert response.status_code == 413
    assert client.post('/mcp/', headers=HEADERS, json=message).status_code == 200


def test_invalid_arguments_and_storage_errors_are_safe(connected, monkeypatch):
    client, services = connected
    result = rpc(client, 'tools/call', {'name': 'get_part', 'arguments': {'part_id': 0}}).json()['result']
    assert result['isError'] and result['structuredContent']['error']['code'] == 'invalid_input'
    def fail(*args):
        raise RuntimeError('private-database-password')
    monkeypatch.setattr(services.domain, 'get', fail)
    response = rpc(client, 'tools/call', {'name': 'get_part', 'arguments': {'part_id': 1}})
    assert response.json()['result']['isError']
    assert 'private-database-password' not in response.text


def test_disabled_mcp_is_not_a_ui_route(tmp_path):
    services = installation(tmp_path)
    ui = tmp_path / 'ui'
    ui.mkdir()
    (ui / 'index.html').write_text('<html>Inventory</html>')
    with TestClient(create_app(services, ui_dist_path=ui)) as client:
        assert client.get('/mcp').status_code == 404
        assert client.post('/mcp').status_code == 405


@pytest.mark.parametrize('token,url', [('', 'https://parts.example'), ('short', 'https://parts.example'),
    (TOKEN, 'http://parts.example'), (TOKEN, 'https://parts.example/mcp'),
    (TOKEN, 'https://user:pass@parts.example'), (TOKEN, 'https://parts.example?token=secret')])
def test_invalid_configuration_fails_closed(token, url):
    with pytest.raises(ValueError):
        MCPConfig(token, url)


def test_local_configuration_enables_mcp_without_model_credentials(tmp_path, monkeypatch):
    import local_app
    repository = TestRepository(tmp_path / 'local')
    config = tmp_path / 'config.toml'
    config.write_text('[db]\nhost="localhost"\ndatabase="test"\nuser="test"\n'
        f'[mcp]\nenabled=true\npublic_url="https://testserver"\ntoken="{TOKEN}"\n')
    monkeypatch.setenv('PARTS_BIN_CONFIG', str(config))
    monkeypatch.setattr(local_app.log, 'init', lambda: None)
    monkeypatch.setattr(local_app, 'PostgresPartsBinRepository', lambda _: repository)
    with TestClient(local_app.create_app()) as client:
        assert not client.get('/health').json()['agent_configured']
        assert {tool['name'] for tool in rpc(client, 'tools/list').json()['result']['tools']} == READ_TOOLS


async def test_official_mcp_client_initializes_pages_and_reads_evidence(tmp_path):
    services = installation(tmp_path)
    services.domain.add_part(AddPartRequest(PartFields.from_mapping({
        'part_category': 'resistor', 'profile': 'passive', 'value': '10k',
        'package': '0805', 'quantity': 2})))
    app = create_app(services, mcp_config=MCPConfig(TOKEN, 'https://testserver'))
    before = _snapshot(services.domain.repository)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                headers={'Authorization': f'Bearer {TOKEN}'}) as http:
            async with streamable_http_client('https://testserver/mcp', http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == READ_TOOLS
                    first = await session.call_tool('search_parts', {'filters': {'part_category': 'resistor'}, 'limit': 1})
                    page = first.structuredContent['result']
                    assert page['count'] == 2 and page['next_offset'] == 1
                    second = await session.call_tool('search_parts', {
                        'filters': {'part_category': 'resistor'}, 'limit': 1, 'offset': page['next_offset']})
                    assert second.structuredContent['result']['parts'][0]['id'] == 2
                    assert second.structuredContent['result']['next_offset'] is None
                    facts = await session.call_tool('get_specifications', {'part_id': 1})
                    assert not facts.isError
                    blocked = await session.call_tool('delete_part', {'part_id': 1})
                    assert blocked.isError
    assert _snapshot(services.domain.repository) == before
