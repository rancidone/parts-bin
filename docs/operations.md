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
PARTS_BIN_SMOKE_RUNTIME=openai uv run pytest tests/e2e/test_agent_runtime_smoke.py
```

See the [smoke test](../tests/e2e/test_agent_runtime_smoke.py) for its scope and required environment variables.

## Diagnosing failures

Start with a small reproducible request and distinguish provider transport,
tool validation, domain errors, and interrupted execution. Read diagnostic
metadata without copying private conversation or inventory contents into reports.

```sh
curl -fsS http://localhost:8000/health
uv run pytest tests/agent_runtime/test_telemetry.py tests/evaluation/test_failures.py tests/test_log.py
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
Historical approval IDs without an execution checkpoint require a fresh proposal.

Agent events carry an execution ID. To resume an interrupted execution, replay
the conversation's events, take the ID of the unfinished request, and submit it
to the resume endpoint rather than sending the same message again:

```sh
curl -N -X POST "http://localhost:8000/agent/threads/$thread_id/resume" \
  -F "execution_id=$execution_id"
```

Use the original approval endpoint with the same request ID for a waiting
decision. Resume preserves model/tool context and pending calls. Replayed events
keep their original sequence; consumers must deduplicate by thread and sequence.
A live worker prevents another worker from claiming the execution. After abrupt
process loss its lease may take five minutes to expire; normal cancellation
releases ownership. An expired worker cannot checkpoint or mutate inventory.

An interrupted supplier lookup reports an incomplete outcome; explicitly ask for
a new lookup if needed. Resume can repeat a model request whose response was not
checkpointed, so check usage when recovering an uncertain paid request. Photo
bytes are never checkpointed. If initial image analysis was interrupted, provide
a fresh photo with resume (`-F "photo=@part.jpg"`); if resuming without the photo
already failed with `image_resubmission_required`, start a new photo message.

Retain execution checkpoints, operation outcomes, and event identity records in
backups alongside inventory and conversations. The UI currently replays visible
history; execution resume is an API procedure, not an automatic browser retry.

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

## Supplied-source cache recovery

The supplied-source operator command uses a disposable SQLite cache in the
configured inventory database. Cache freshness is separate from retention of
accepted evidence. Preserve authoritative tables during recovery; deleting the
whole database to clear a cache would also delete inventory and retry protection.

A failed or interrupted supplied-source lookup leaves a five-minute claim from
the attempt's start. A lookup during that interval, including `--refresh`, is
blocked. Once it expires, retry by explicitly invoking the operator command;
there is no automatic retry or durable cache job. A failure is not saved as a
no-match result. Check provider usage before retrying uncertain paid extraction.

`--refresh` bypasses freshness and downloads the source again. If its hash is
unchanged, the saved extraction can be reused without another model call. A
failed refresh clears the prior cached result, so a later attempt may incur a
new extraction charge. Cache cleanup cannot remove evidence already staged for
review or accepted into inventory. Downloaded PDFs and photos are not cache data.

## Turning failures into regressions

Capture only diagnostic metadata, then construct a synthetic scenario that
reproduces the failure without private user content. Review the scenario before
promoting it into the evaluation set:

```sh
uv run python -m evaluation.failures capture --help
uv run python -m evaluation.failures promote --help
uv run pytest tests/evaluation
```

See [evaluation usage](../evaluation/README.md) for running checks and
[evaluation decisions](design/evaluation.md) for what they can establish.
