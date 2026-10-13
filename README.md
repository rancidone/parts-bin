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
chmod 600 config.toml
```

Skip the copy if you already have local configuration. Edit `config.toml` to
configure the OpenAI API agent; use
[config.example.toml](config.example.toml) for configuration keys. Keep credentials
out of version control. See [operations](docs/operations.md) for recovery and diagnostics.

Use an existing PostgreSQL server with a dedicated `parts_bin` database and login.
Set `[db]` to that installation; use a protected configuration or `PGPASSFILE` for
the password. Initialize its schema explicitly before startup:

```sh
uv run python -m db.postgres.setup --host localhost --database parts_bin --user parts_bin
```

SQLite installations require the explicit [migration procedure](docs/operations.md#sqlite-migration).
The app does not create databases, migrate data, or fall back to SQLite on startup.

Run the API and UI in separate terminals:

```sh
uv run uvicorn local_app:create_app --factory --host 127.0.0.1 --port 8000
```

The local factory loads configuration and constructs PostgreSQL-backed services.
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

The UI uses hash routes: `/#/` for chat, `/#/inventory` for inventory, and
`/#/inventory/42` for part 42. These links support browser Back, Forward,
bookmarks, and reloads without colliding with the inventory API routes.

Use − / + on part cards or inventory rows to save a one-unit stock change.
These controls adjust the current stored count and preserve pending spec reviews.
For an exact count or metadata changes, choose Edit part in inventory, then use
the labeled form and Save changes. Cancel discards the form edits.
Every category supports an optional HTTPS datasheet link in Edit part. Inventory
shows saved links and includes them in CSV exports. Fetch specs uses the saved
link to stage electrical facts for supported categories, including op-amps.
Open Electrical specifications to inspect accepted facts or restore a pending
review after reloading. Review evidence and choose Accept electrical specs or
Dismiss electrical review; refreshing does not change stock or accept facts.

Ask the chat to find stock by description, manufacturer, or a fragment of a part
number, such as `PBSS5350T`. Candidate search keeps distinct ordering suffixes
and quantities separate. Markings are discoverable when recorded in a description
or part number; unrecorded markings need clarification. Partial matches identify
candidates and do not establish interchangeable stock or electrical suitability.
Exact ordering-code lookup remains available separately.

Paste a BOM (including CSV or tab-separated rows) or a list of part numbers into
chat to compare it with inventory. Include required quantities when known. Chat
reports exact matches, missing items, zero stock, and quantity shortages, keeping
ordering suffixes and package variants separate. Generic passive rows need a
category and value with units; ambiguous rows need clarification. Repeated
requirements are combined before checking. Availability checks do not reserve
stock or change inventory, and nominal matches do not verify electrical ratings.

Inventory and candidate searches return bounded pages ordered by part ID. Follow
`next_offset` with the same search arguments and limit until it is null. Counts
cover the full search. Specification searches page confirmed matches and incomplete
candidates separately, returning up to the limit from each group. Paging reads
current committed data; restart if inventory or accepted facts change while paging.

In Inventory, choose **Filter electrical specs**, select a category, and add
requirements using the supported fields and rating bases. Include units and the
complete test conditions, one `condition=value` per line. **Search accepted
specifications** shows confirmed matches with evidence separately from candidates
that need evidence or matching conditions. Pending reviews and user assertions
cannot confirm a match. **Next results** pages both groups; restart the search
after changing stock or accepting facts. Open a result's part link to inspect or
edit it, or choose **Browse inventory** to return to the stock table. These filters
read committed inventory without model or supplier calls.

## Development checks

Python tests live in `tests/`, mirroring the source directories. Pytest discovers
the suite there; run a focused subset with, for example, `uv run pytest tests/domain`.

Use a separate disposable PostgreSQL database whose login can create schemas.
Tests create isolated schemas and remove them after the session. Never point the
following setting at a production database. `PGPASSFILE` can supply its password:

```sh
export PARTS_BIN_TEST_POSTGRES_DSN='host=localhost dbname=parts_bin_test user=parts_bin_test'
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
