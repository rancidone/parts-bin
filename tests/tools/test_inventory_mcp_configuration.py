"""MCP setup keeps configuration private and preserves other installation settings."""

import stat
import tomllib

import pytest

from scripts.inventory_mcp import enable


@pytest.mark.parametrize('section', ['', '\n[mcp]\nenabled = false\ntoken = ""\npublic_url = "https://parts.example"\n'])
def test_setup_is_private_idempotent_and_preserves_other_sections(tmp_path, capsys, section):
    path = tmp_path / 'config.toml'
    path.write_text('[db]\nhost = "localhost"\n' + section + '\n[digikey]\nclient_id = "private-client"\n')
    enable(path, 'https://parts.example')
    first = path.read_text()
    config = tomllib.loads(first)
    assert len(config['mcp']['token']) >= 32
    assert config['mcp']['enabled']
    assert config['db'] == {'host': 'localhost'}
    assert config['digikey'] == {'client_id': 'private-client'}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    enable(path, 'https://parts.example/')
    assert path.read_text() == first
    output = capsys.readouterr().out
    assert config['mcp']['token'] not in output and 'private-client' not in output


def test_setup_refuses_implicit_origin_change_and_interrupted_file(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[db]\nhost = "localhost"\n')
    pending = path.with_name('config.toml.mcp.pending')
    pending.write_text('protected recovery state')
    original = path.read_text()
    with pytest.raises(FileExistsError):
        enable(path, 'https://parts.example')
    assert path.read_text() == original
    pending.unlink()
    enable(path, 'https://parts.example')
    original = path.read_text()
    with pytest.raises(ValueError, match='another origin'):
        enable(path, 'https://other.example')
    assert path.read_text() == original
