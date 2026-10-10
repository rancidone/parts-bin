# Parts Bin

Parts Bin helps an electronics hobbyist identify parts, manage stock, and find
what they already have through text, photos, and an inventory UI.

The project favors local-first operation and independent deployments owned by
the operator. The cloud direction is AWS serverless with Terraform; see the
[cloud decisions](docs/design/cloud-hosting.md) for rationale and open choices.
A design decision is not a claim that its implementation is available.

## Local setup

Use Python 3.14+ with [uv](https://docs.astral.sh/uv/) and Node.js with npm.
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
out of version control. See [configuration decisions](docs/design/configuration.md)
and [operations](docs/operations.md) for guidance.

Run the API and UI in separate terminals:

```sh
uv run uvicorn local_app:create_app --factory --host 127.0.0.1 --port 8000
```

The local factory loads configuration and constructs SQLite-backed services.
The HTTP adapter can also be constructed with injected services; see
[application assembly](application.py) and [architecture](docs/design/architecture.md#http-and-worker-execution).

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

## Development checks

Python tests live in `tests/`, mirroring the source directories. Pytest discovers
the suite there; run a focused subset with, for example, `uv run pytest tests/domain`.

```sh
uv run pytest
npm run lint --prefix ui
npm run build --prefix ui
```

See [evaluation guidance](evaluation/README.md) for focused checks and their
limitations. Run checks to obtain results; documentation does not certify them.

## Project understanding

Start with the [documentation guide](docs/README.md) for product intent,
architecture decisions, enrichment, and cloud tradeoffs. [TODO.md](TODO.md)
contains upcoming work, not a record of completed implementation.
