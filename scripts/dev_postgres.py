"""Set up the repo's local PostgreSQL container and migrate local SQLite snapshots.

Run with uv run python -m scripts.dev_postgres. Requires Docker Compose.
"""
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tomllib

from db.postgres import PostgresConfig, PostgresPartsBinRepository
from db.postgres.migrate import import_snapshots, verify_snapshots
from db.postgres.setup import initialize


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    data = root / 'data'
    data.mkdir(exist_ok=True)
    secret = data / 'dev-postgres-password'
    if not secret.exists():
        with secret.open('x') as file:
            file.write(secrets.token_urlsafe(40) + '\n')
    secret.chmod(0o600)
    password = secret.read_text().strip()
    pgpass = data / 'dev-postgres.pgpass'
    pgpass.write_text(f'127.0.0.1:54329:*:parts_bin:{password}\n127.0.0.1:54330:*:parts_bin_test:{password}\n')
    pgpass.chmod(0o600)
    config_path = root / 'config.toml'
    if not config_path.exists():
        shutil.copyfile(root / 'config.example.toml', config_path)
    original = config_path.read_text()
    config = tomllib.loads(original)
    db = config['db']
    if 'path' not in db and not (
        db.get('host') in {'localhost', '127.0.0.1'} and db.get('database') == 'parts_bin'
        and db.get('user') == 'parts_bin' and db.get('port', 5432) in {5432, 54329}
    ):
        raise ValueError('Local setup requires a SQLite source, sample config, or existing development configuration')
    subprocess.run(['docker', 'compose', '-f', 'compose.postgres.yaml', 'up', '-d', '--wait', 'postgres'], check=True)
    target = PostgresConfig('127.0.0.1', 'parts_bin', 'parts_bin', port=54329, password=password, sslmode='disable')
    initialize(target.connect)
    if config['db'].get('path'):
        backup = data / ('pre-postgres-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.mkdir(mode=0o700)
        shutil.copyfile(config_path, backup / 'config.toml')
        (backup / 'config.toml').chmod(0o600)
        source = (root / config['db']['path']).resolve()
        history = (root / config.get('agent', {}).get('conversation_db_path', config['db']['path'])).resolve()
        if source != history:
            raise RuntimeError('Stop writers and follow the coordinated separate-file migration in docs/operations.md')
        if source.exists():
            snapshot = backup / 'parts.db'
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as connection, sqlite3.connect(snapshot) as destination:
                connection.backup(destination)
            snapshot.chmod(0o600)
            repository = PostgresPartsBinRepository(target)
            counts = import_snapshots(repository, snapshot)
            verify_snapshots(repository, snapshot)
            print('Local snapshot imported and verified:', counts)
    block = ('[db]\nhost = "127.0.0.1"\ndatabase = "parts_bin"\nuser = "parts_bin"\n'
             f'port = 54329\nsslmode = "disable"\npassword = "{password}"\n\n')
    updated = re.sub(r'^\[db\]\s*\n.*?(?=^\[|\Z)', lambda _: block, original, flags=re.M | re.S)
    updated = re.sub(r'^conversation_db_path\s*=.*\n', '', updated, flags=re.M)
    assert tomllib.loads(updated)['db']['port'] == 54329
    pending = data / 'config.postgres.pending.toml'
    pending.write_text(updated)
    pending.chmod(0o600)
    pending.replace(config_path)
    print('Local PostgreSQL is ready at 127.0.0.1:54329; original SQLite files are preserved.')


if __name__ == '__main__':
    main()
