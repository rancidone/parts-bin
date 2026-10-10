# Operations

See [the README](../README.md) for startup and
[config.example.toml](../config.example.toml) for settings. `PARTS_BIN_CONFIG`
selects a configuration file outside the checkout. Keep configuration private.
The infra repository owns deployment to core; this repository owns the app image
and Compose definition.

## Diagnostics

```sh
curl -fsS http://localhost:8000/health
docker compose logs --tail 100 parts-bin
```

`agent_configured` indicates that a key is present, not that provider authentication
works. A new chat request checks the provider and may incur charges. For an
isolated live smoke test, follow [the test's environment requirements](../tests/e2e/test_agent_runtime_smoke.py).
For model or lookup failures, see [evaluation](../evaluation/README.md).

## Interrupted requests

**Refresh history** reads saved events without repeating work. **Resume request**
explicitly continues the original execution after refreshing history. An active
worker can prevent resume; after abrupt process loss its lease can take five
minutes to expire. Do not resend the original message to recover a mutation.

Use the original approval controls and request ID for a waiting decision.
Duplicate delivery returns the saved outcome. A changed target or an unknown
historical approval requires a fresh proposal; conversation prose cannot replace
approval.

Resume may repeat a model call interrupted before its response was saved. Check
provider usage before retrying uncertain paid work. Interrupted supplier retrieval
is reported as incomplete and needs an explicit new lookup. Photos are not saved:
attach a fresh photo before resuming interrupted initial image analysis. If resume
has already failed with `image_resubmission_required`, send a new photo message.
Old provider conversations remain read-only; start a new OpenAI chat.

For API recovery, retain the thread ID and use the unfinished execution ID from
its events:

```sh
curl -N -X POST "http://localhost:8000/agent/threads/$thread_id/resume" \
  -F "execution_id=$execution_id"
```

Add `-F "photo=@part.jpg"` when resubmitting an image. **New chat** changes the
browser's current thread without deleting server history.

The conversation selector lists retained history and its original provider.
Switching conversations only reads saved events; it never resends a message,
resumes work, or submits an approval decision. Retired-provider history remains
selectable but read-only.

## Identifying an unknown component

Ask for identification before requesting a stock change. Identification explains
visible markings and uncertainty without adding stock. For an ambiguous label,
supply a manufacturer, product link, or clear package markings. A package you
provide remains a user assertion until verified against evidence; quantity is
needed only when you request adding stock.

## Model context limits

Saved chat history remains complete; the model sees only recent complete turns.
When a reference depends on older context, restate the relevant details. Photos
are analyzed on the first model call; attach a new photo for another visual
inspection.

If a tool result is omitted from model context, inspect its saved result in chat
history. Narrow read filters or request a smaller page. Pending-review discovery
supports exact ordering codes, equivalent nominal values, and individual part IDs;
provenance is available for an individual review.

`context_budget_exceeded` and `model_response_incomplete` end an execution without
automatic retry. Inspect saved outcomes and stock before starting a narrower
request: completed mutations remain committed. Refresh history to recover a missed
acknowledgement; resuming a terminal execution returns the saved outcome.

Supplied-PDF extraction uses selected source excerpts. If these cannot establish
identity or evidence, provide a shorter datasheet for the exact ordering variant.
Missing evidence in selected excerpts does not establish that the part is absent
from the source. Manufacturer downloads identify Parts Bin in the
request headers. Download, parsing and model excerpt limits remain independent;
see [the extraction implementation](../ingestion/supplied_source.py) for current
bounds. An HTTP or parsing failure stops before a paid extraction and must not be
reported as no matching part. No automatic retry substitutes a different source.

## Backup and recovery

Find the inventory and conversation database paths in your private configuration.
Back up both if separate; quiesce writes for a coordinated snapshot. Inventory
backups must include provenance, pending reviews, approvals, execution checkpoints,
and mutation outcomes. These records protect against repeated effects.

Use SQLite's online backup facility to include WAL contents. Substitute your
configured paths and a new destination for each snapshot:

```sh
umask 077
mkdir -p backups
sqlite3 data/parts.db ".backup 'backups/parts-recovery.db'"
sqlite3 backups/parts-recovery.db 'PRAGMA integrity_check;'
```

Protect configuration separately. Keep an off-host recovery copy and choose
retention for your acceptable data loss. Photos are ephemeral and must not enter
backups. Supplier caches are not an inventory backup.

Rehearse restoration in an isolated installation. Stop the target app, preserve
its existing database and WAL/SHM files together, and install the snapshot at the
configured path with the correct ownership. Never combine a restored database
with stale WAL/SHM files. Check integrity, stock quantities, conversation history,
and a read/write workflow before using it. Keep the original installation intact
until recovery is verified.

Before upgrading, retain the previous code/image and take a backup. Code rollback
and data restoration are separate: an older image may not understand a newer
schema, and restoring a snapshot loses subsequent writes.

## Electrical fact review

Chat exposes supported fields through `get_specification_contract`. Assertions
can be proposed and approved in chat but remain labeled as user assertions.
For sourced ratings, inspect the exact variant and supporting document, then
prepare facts using [the validated contract](../domain/specifications.py).

Stage an inspected JSON array on an isolated inventory copy first:

```sh
uv run python -m ingestion.review_specifications PART_ID /path/to/candidate.json \
  --database /path/to/isolated-parts.db
```

This stages a review; it does not retrieve or authenticate evidence. Inspect with
`get_specifications`, then request `apply_specification_review` for approval or
`reject_specification_review` to discard it. Supplier metadata lookup does not yet
extract electrical facts automatically. See [the specification decisions](adr/0007-reviewed-electrical-facts.md).

## Supplied-source retries

The supplied-source command stages metadata from a supplied manufacturer PDF:

```sh
uv run python -m ingestion.enrich_source PART_ID SOURCE_URL --model MODEL
```

Its disposable cache shares the local inventory database. Never delete that
database to clear a cache. A failed or interrupted attempt blocks retries,
including `--refresh`, for five minutes from its start. Afterward, retry explicitly;
failure is not a no-match result.

`--refresh` downloads again; an unchanged hash can reuse the extraction without a
model call. A failed refresh clears the prior result, so retry may incur another
charge. Cache expiry does not affect staged or accepted evidence.
