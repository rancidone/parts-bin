# Operating Parts Bin locally

This guide explains useful operating procedures and their purpose. It is not a
record of successful deployments or recovery exercises. Use the code, tests, and
output of the exercise you run to establish behavior for your checkout.

## Configuration and runtime checks

Follow the [setup guide](../README.md). Use [config.example.toml](../config.example.toml)
for keys and [compose.yaml](../compose.yaml) for container mounts and environment
variables rather than copying a configuration schema from this guide.

Configure the OpenAI API agent through private configuration. Use
`PARTS_BIN_CONFIG` to point the process at a configuration file outside the checkout
when needed. Keep credentials out of logs, prompts, shell history, and fixtures.

For a checkout that previously used another runtime, configure OpenAI and start a
new conversation. Keep historical conversation data and any old provider session
files as retained local data; do not rewrite their provider identity or delete them
as part of application cleanup. Retired-provider threads are for reading history.

An HTTP health response is a useful diagnostic, not proof of provider login or
model quality. Exercise a new OpenAI thread. A live smoke
check may incur provider charges:

```sh
# Supply OPENAI_API_KEY privately and choose PARTS_BIN_OPENAI_MODEL first.
PARTS_BIN_SMOKE_RUNTIME=openai uv run pytest e2e/test_agent_runtime_smoke.py
```

See the [smoke test](../e2e/test_agent_runtime_smoke.py) for its scope and required environment variables.

## Diagnosing failures

Start with a small reproducible request and distinguish provider transport,
tool validation, domain errors, and interrupted execution. Read diagnostic
metadata without copying private conversation or inventory contents into reports.

```sh
curl -fsS http://localhost:8000/health
uv run pytest agent_runtime/test_telemetry.py evaluation/test_failures.py test_log.py
sqlite3 data/parts.db 'PRAGMA integrity_check;'
```

Substitute your configured database path. Inspect [logging](../log.py),
[telemetry](../agent_runtime/telemetry.py), and container configuration for event
formats and destinations. Avoid maintaining a separate event/schema list here.
An integrity check tests database consistency, not completeness of a backup or
correctness of the inventory it contains.

## Backup and recovery

Approval requests, decisions, and saved mutation outcomes live with inventory,
even when conversations use a separate database. Retain them together: removing
operation records can remove the protection against duplicate approval delivery.
After a restart, resubmit the same approval ID to recover its saved outcome.
If inventory or pending evidence changed while approval was waiting, request a
fresh proposal and review it again. An opposite decision cannot replace a recorded
approval or denial.

Proposals created by older versions with in-memory approvals cannot be safely
reconstructed from conversation prose or events. Ask the assistant for a fresh
proposal if an old approval ID is unknown; do not infer consent from history.
These procedures recover approval operations, not an interrupted model turn.

Identify the inventory and conversation database paths from your configuration.
They may share a file. Protect configuration separately and retain historical
provider session files if they are part of your recovery requirements. A supplier cache is not an inventory
backup. If state spans multiple files, quiesce writes for a coordinated backup.

For a single SQLite database, use SQLite's online backup facility rather than
copying a live database file and hoping its WAL state is included:

```sh
mkdir -p backups
sqlite3 data/parts.db ".backup 'backups/parts-recovery.db'"
sqlite3 backups/parts-recovery.db 'PRAGMA integrity_check;'
```

Use a new destination for each retained backup; do not overwrite your only known
good copy. Apply restrictive filesystem permissions and keep a protected copy
outside the application's failure domain. Retention and off-host storage depend
on the deployment and recovery objectives.

Rehearse recovery into an isolated directory using copies of the backup and
configuration. Stop the target application before replacing database state.
Preserve any old database and its associated WAL/SHM files together for
investigation; do not combine an old WAL with a restored database. Install the
backup at the configured path with appropriate ownership and permissions before
starting the target application.

Check integrity, representative inventory quantities, conversation history, and
an application read/write workflow in that isolated installation. Record elapsed
recovery time and the backup's age with the exercise. Keep the original local
data untouched until recovery and any migration have been reviewed.

## Turning failures into regressions

Capture only diagnostic metadata, then construct a synthetic scenario that
reproduces the failure without private user content. Review the scenario before
promoting it into the evaluation set:

```sh
uv run python -m evaluation.failures capture --help
uv run python -m evaluation.failures promote --help
uv run pytest evaluation
```

See [evaluation usage](../evaluation/README.md) for running checks and
[evaluation decisions](design/evaluation.md) for what they can establish.
