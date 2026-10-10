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
backups alongside inventory and conversations. The chat remembers the current
conversation ID in browser storage and restores its history on reload. It stores
no conversation text or photos in browser storage. Use **Refresh history** after a
connection failure; replay only reads saved events. **Resume request** explicitly
continues unfinished work using its original execution ID. It refreshes history
first and does not issue a resume if completion has appeared in the meantime.
An unfinished request may still have an active worker; refresh its progress and
wait for the lease to release before retrying a rejected resume.

For an interrupted initial photo analysis, attach a fresh photo before choosing
**Resume request**. Approval requests use their original controls; completed
decisions are shown as approved or declined. **New chat** changes the current
browser pointer without deleting retained server history. Conversation selection
is separate UI work; retain the thread ID when you need API access to an older
conversation. Retired-provider history is read-only.

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

## Historical passive values

Adding or editing a passive normalizes supported value spellings in the domain.
For example, editing a resistor to `22K` stores `22k`, and category-specific search
recognizes either spelling. Unknown units are preserved rather than assigned an
inferred electrical value. This is spelling normalization; it does not establish
verified tolerance, power, or other specifications.

Existing rows are not rewritten when the application starts. Search compares
historical value spellings without changing their timestamps or evidence. If
several records share a normalized identity, stock increments report a conflict
with the affected IDs. Inspect the records and their provenance, then explicitly
resolve their identities; do not automatically combine quantities or delete
records. An edit that would collide also reports a conflict. Unknown package and
known package stock remain separate identities.

## Reviewing electrical facts

Use `get_specification_contract` in chat to discover supported fields for a
category. User-provided ratings can be staged through chat as assertions, then
accepted with `apply_specification_review`. They remain assertions after approval.
Search cannot treat them as independently sourced facts.

For sourced facts, inspect the exact ordering variant and supporting document
first. Prepare a JSON array matching the validated fact contract in
[domain/specifications.py](../domain/specifications.py), with the original units,
qualifiers, all applicable conditions, and a bounded passage for each field.
Source evidence needs its URL, page, content hash, retrieval timestamp, and exact
ordering code. Do not include downloaded document content or photos. Use an
isolated inventory copy to rehearse the import:

```sh
uv run python -m ingestion.review_specifications PART_ID /path/to/candidate.json \
  --database /path/to/isolated-parts.db
```

The command stages only. It makes no retrieval or model call and does not verify
passage authenticity. Inspect the proposal with `get_specifications`, compare it
against the source, and request `apply_specification_review` in chat for the
approval controls. Use `reject_specification_review` to discard a proposal. Chat
shows original values, bases, conditions, passages, and source links during review.

Search with explicit requirements and stock, for example four resistors at 10 kΩ,
tolerance at most 1%, and rated power at least 0.25 W under the source's stated
conditions. Missing evidence or differing conditions produce incomplete candidates.
The current supplier lookup does not automatically extract these electrical facts.
See [the specification decisions](design/electrical-specifications.md) for the
initial categories, unsupported fields, and conservative comparison rules.

Retain electrical facts and pending specification reviews with the inventory
backup. They are authoritative data, independent of enrichment cache freshness.
For a different rated variant, create a distinct inventory record; do not change
an evidenced record's identity or automatically merge stock by value and package.
Use an explicit `add_stock` target only when the added stock is the same variant.

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
