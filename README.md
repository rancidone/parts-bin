# Parts Bin

Parts Bin helps an electronics hobbyist identify parts, manage stock, and find
what they already have through text, photos, and an inventory UI.

The project favors local-first operation and independent deployments owned by
the operator. The cloud direction is AWS serverless with Terraform; see the
[cloud direction](docs/adr/0008-deferred-aws-direction.md) for rationale and open choices.

## Local setup

Use Python 3.14+ with [uv](https://docs.astral.sh/uv/) and Node.js 22.12+ with npm.
Dependency declarations live in [pyproject.toml](pyproject.toml) and
[ui/package.json](ui/package.json).

From the repository root:

```sh
uv sync
npm ci --prefix ui
cp config.example.toml config.toml
mkdir -p data
```

Skip the copy if you already have local configuration. Edit `config.toml` to
configure the OpenAI API agent; use
[config.example.toml](config.example.toml) for configuration keys. Keep credentials
out of version control. See [operations](docs/operations.md) for recovery and diagnostics.

Run the API and UI in separate terminals:

```sh
uv run uvicorn local_app:create_app --factory --host 127.0.0.1 --port 8000
```

The local factory loads configuration and constructs SQLite-backed services.
The HTTP adapter can also be constructed with injected services; see
[application assembly](application.py) and [hosting boundaries](docs/adr/0001-domain-and-storage-boundaries.md).

```sh
npm run dev --prefix ui -- --host 127.0.0.1
```

Open `http://localhost:5173`. Stop each process with Ctrl-C. For a container-based
local deployment, inspect [compose.yaml](compose.yaml), configure its mounts and
API credentials, then run:

```sh
docker compose up --build -d
```

Compose publishes port 8000. Treat this as local setup, not a recipe for exposing
an authenticated service to the internet. Keep configuration and persistent data
outside the image; see [backup and recovery](docs/operations.md#backup-and-recovery).

## Inventory lookup

Ask the chat to find stock by description, manufacturer, or a fragment of a part
number, such as `PBSS5350T`. Candidate search keeps distinct ordering suffixes
and quantities separate. Markings are discoverable when recorded in a description
or part number; unrecorded markings need clarification. Partial matches identify
candidates and do not establish interchangeable stock or electrical suitability.
Exact ordering-code lookup remains available separately.

## Development checks

Python tests live in `tests/`, mirroring the source directories. Pytest discovers
the suite there; run a focused subset with, for example, `uv run pytest tests/domain`.

```sh
uv run pytest
npm run lint --prefix ui
npm run test --prefix ui
npm run build --prefix ui
```

Browser regression tests run the UI against synthetic API responses in Chromium.
They require no backend, local inventory, or provider credentials:

```sh
npm exec --prefix ui -- playwright install chromium
npm run test:browser --prefix ui
```

See [evaluation guidance](evaluation/README.md) for focused checks and their
limitations.

## Project understanding

Start with the [documentation guide](docs/README.md) for product intent,
architecture decisions, enrichment, and cloud tradeoffs. [TODO.md](TODO.md)
contains upcoming work, not a record of completed implementation.
