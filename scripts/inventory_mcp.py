"""Enable read-only MCP with a dedicated secret saved only in protected config."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import tomllib

from tools.inventory_mcp import MCPConfig


def enable(config_path: Path, public_url: str) -> None:
    original = config_path.read_text()
    config = tomllib.loads(original)
    existing = config.get('mcp', {})
    if existing.get('token') and existing.get('public_url', '').rstrip('/') != public_url.rstrip('/'):
        raise ValueError('MCP is already configured for another origin; inspect configuration explicitly')
    token = existing.get('token') or secrets.token_urlsafe(32)
    settings = MCPConfig(token, public_url.rstrip('/'))
    if existing.get('enabled') and existing.get('token'):
        config_path.chmod(0o600)
        print('MCP is already enabled. Existing token preserved.')
        return
    section = ('[mcp]\nenabled = true\n'
               f'public_url = {json.dumps(settings.public_url)}\n'
               f'token = {json.dumps(settings.token)}\n\n')
    pattern = r'^\[mcp\][^\n]*\n.*?(?=^\[|\Z)'
    updated, replaced = re.subn(pattern, lambda _: section, original, count=1, flags=re.M | re.S)
    if not replaced:
        updated = original.rstrip() + '\n\n' + section
    parsed = tomllib.loads(updated)
    assert parsed['mcp'] == {'enabled': True, 'public_url': settings.public_url, 'token': token}
    assert {k:v for k,v in parsed.items() if k != 'mcp'} == {k:v for k,v in config.items() if k != 'mcp'}
    pending = config_path.with_name(config_path.name + '.mcp.pending')
    previous = config_path.stat()
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(updated)
        output.flush()
        os.fsync(output.fileno())
    os.chown(pending, previous.st_uid, previous.st_gid)
    pending.replace(config_path)
    print('Read-only MCP enabled; dedicated token saved in protected config. Recreate HTTP intake.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.toml'))
    parser.add_argument('--public-url', required=True, help='Installation HTTPS origin, without /mcp')
    args = parser.parse_args()
    enable(args.config, args.public_url)


if __name__ == '__main__':
    main()
