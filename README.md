# Parts Bin

Parts Bin helps an electronics hobbyist identify parts, manage stock, and find
what they already have through text, photos, and an inventory UI.

The project favors local-first operation and independent deployments owned by
the operator, using Docker and PostgreSQL on existing infrastructure. Execution
should remain compatible with short-lived, replaceable workers, while cloud
deployment is outside the roadmap. See [the hosting direction](docs/adr/0033-serverless-compatible-self-hosting.md).

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

For a local Docker PostgreSQL server, run:

```sh
uv run python -m scripts.dev_postgres
```

This starts the pinned development database on `127.0.0.1:54329`, initializes
its schema and updates your untracked `config.toml`. Existing SQLite data is
snapshotted, imported and verified before configuration changes. Stop any old
local app first. Generated passwords and snapshots stay in ignored `data/`.
The named volume preserves development stock when the container is stopped.

Alternatively, set `[db]` for an existing PostgreSQL database/login, use protected
configuration or `PGPASSFILE` for authentication, and initialize its schema:

```sh
uv run python -m db.postgres.setup --host localhost --database parts_bin --user parts_bin
```

Follow [migration operations](docs/operations.md#sqlite-migration) for other
SQLite installations. Startup never initializes data or falls back to SQLite.

Run the API, durable worker and UI in separate terminals:

```sh
uv run uvicorn local_app:create_app --factory --host 127.0.0.1 --port 8000
```

```sh
uv run python -m agent_runtime.worker
```

After upgrades, run `uv run python -m db.postgres.setup --config config.toml`
before starting either process. Local development can reconcile by polling; public
installations can also receive signed OpenAI webhooks.

PostgreSQL is the only runtime persistence adapter; installation requires no AWS
SDK or IAM database login. The local factory loads configuration and constructs
PostgreSQL-backed services.
The HTTP adapter can also be constructed with injected services; see
[application assembly](application.py) and [hosting boundaries](docs/adr/0001-domain-and-storage-boundaries.md).

```sh
npm run dev --prefix ui -- --host 127.0.0.1
```

Open `http://localhost:5173`. Stop each process with Ctrl-C. For a container-based
local deployment, inspect [compose.yaml](compose.yaml), configure its mounts,
API credentials and a PostgreSQL hostname reachable from the app container
(for example, `postgres` on a shared Docker network), then run:

```sh
docker compose up --build -d
```

Compose publishes port 8000. Treat this as local setup, not a recipe for exposing
an authenticated service to the internet. Keep configuration and persistent data
outside the image; see [backup and recovery](docs/operations.md#backup-and-recovery).

## Inventory lookup

In Inventory, enter terms in **Filter inventory** to find candidates directly,
without chat, model calls or supplier retrieval. Choose a **Category** and set
**Minimum stock** to narrow the results; stock applies to each individual record.
Add **Nominal value** for category-aware matching (for example, `10 kΩ` finds
resistors stored as `10k`, and `0.1 µF` finds capacitors stored as `100n`). Choose
a category to disambiguate bare values. Other categories compare values exactly.
**Exact package** matches stored spelling, including case and punctuation:
`SOT23` and `SOT-23` remain distinct. These filters combine with text terms and
work without a text query. Text terms remain literal; use Nominal value for unit
equivalence. Only matching records are paged; nearby nominal values are excluded. **Clear filters** restores
full browsing. Filtered results show totals and **Previous results** / **Next
results** controls. Sorting and CSV export cover the visible page. Pending review
text is excluded; candidates do not establish interchangeable parts or electrical
suitability. After inventory changes, refresh or restart pagination.

Bookmark or copy the Inventory URL to save its query, category, nominal value, exact package,
minimum stock, and page. The `value` and `package` URL parameters preserve these
new filters. Reload and browser Back/Forward restore those controls and read current
inventory. For example, `/#/inventory?query=SOT23&category=mosfet&minimum_quantity=1&page=2`
opens the second page of that search. Pages start at 1, with 50 records per filtered
page; defaults are omitted from links. Changing a filter returns to the first page.
Typing in one field creates one history step; category changes, paging and clearing
filters create separate steps. If a saved page no longer has results, choose
**First results**. Invalid page values fall back to the first page; invalid stock
or oversized query, nominal-value or package inputs show validation errors. Direct part links remain separate.
These URLs save search criteria, not a snapshot of stock or electrical filters.

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
number, such as `PBSS5350T`. Combine terms such as `Nexperia 5350 SOT23`
to narrow a candidate search across the same part's manufacturer, number, package,
category, value and description. Each whitespace-separated term must match that
record; punctuation stays literal. Nominal-unit equivalence still uses structured
value filters rather than text fragments. Candidate search keeps distinct ordering suffixes
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

Use the ephemeral PostgreSQL test container; its database lives in tmpfs and
is discarded when the container is removed. It has a separate loopback port and
login. Run local setup once to generate the protected password file:

```sh
docker compose -f compose.postgres.yaml --profile test up -d --wait postgres-test
export PGPASSFILE="$PWD/data/dev-postgres.pgpass"
export PARTS_BIN_TEST_POSTGRES_DSN='host=127.0.0.1 port=54330 dbname=parts_bin_test user=parts_bin_test'
uv run pytest
docker compose -f compose.postgres.yaml --profile test rm -s -f postgres-test
npm run lint --prefix ui
npm run test --prefix ui
npm run build --prefix ui
```

Tests also create and remove isolated schemas per session. You can supply your
own dedicated disposable database through the same environment setting; its
login needs schema creation privileges. Never use a production database.

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
architecture decisions, enrichment, and deployment boundaries. [TODO.md](TODO.md)
contains upcoming work, not a record of completed implementation.
