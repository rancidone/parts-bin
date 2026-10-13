"""Register this installation's OpenAI webhook and protect its signing secret.

Run once on the deployment host, then restart the HTTP service. Existing endpoint
secrets are never rotated implicitly. No API keys or signing secrets are printed.
"""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from urllib.parse import urlparse

from openai import OpenAI
from agent_runtime.dispatch import TERMINAL_EVENTS


def register(config_path: Path, url: str):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('A public HTTPS callback URL without credentials is required')
    pending = config_path.with_name(config_path.name + '.webhook.pending')
    if pending.exists():
        raise ValueError('Protected pending config already exists; inspect it before registration')
    original = config_path.read_text()
    config = tomllib.loads(original)
    settings = config['agent']['openai']
    if settings.get('webhook_secret'):
        raise ValueError('Webhook signing secret already configured; inspect the existing endpoint instead of creating another')
    with OpenAI(api_key=settings['api_key'], base_url=settings.get('base_url', 'https://api.openai.com/v1'),
                max_retries=0) as client:
        if any(endpoint.url == url for endpoint in client.webhooks.list()):
            raise ValueError('Endpoint already exists; recover its original signing secret or explicitly rotate it in the OpenAI dashboard')
        endpoint = client.webhooks.create(name='Parts Bin durable responses', url=url, event_types=sorted(TERMINAL_EVENTS))
    # Protect a recovery copy before changing the live configuration. If a write
    # fails after creation, recover this file rather than registering again.
    secret_lines = f'webhook_secret = {json.dumps(endpoint.signing_secret)}\nwebhook_endpoint_id = {json.dumps(endpoint.id)}\n'
    updated = re.sub(r'(^\[agent\.openai\][^\n]*\n)', lambda match: match[1] + secret_lines, original, count=1, flags=re.M)
    assert tomllib.loads(updated)['agent']['openai']['webhook_secret'] == endpoint.signing_secret
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(updated)
        output.flush()
        os.fsync(output.fileno())
    previous = config_path.stat()
    os.chown(pending, previous.st_uid, previous.st_gid)
    pending.chmod(stat.S_IMODE(previous.st_mode) & 0o600)
    pending.replace(config_path)
    print('Webhook registered and signing secret saved in protected configuration. Restart HTTP intake.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.toml'))
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    register(args.config, args.url)


if __name__ == '__main__':
    main()
