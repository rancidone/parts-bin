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

Inventory Refresh first requests a single DigiKey product. If that lookup cannot
resolve the identifier, it performs a bounded supplier keyword search and shows
possible identities for clarification. Search results can include unrelated
parts and do not establish the identity of your stock. Confirm the full marking
and manufacturer, edit the inventory identifier through the normal review flow,
then retry. Candidate discovery does not stage variant metadata or change stock.

When a part has a saved datasheet link, Inventory Fetch specs instead uses that
source through the supplied-PDF electrical extractor. All categories can store
an HTTPS link; automatic extraction still requires a recorded part number and
a supported electrical category. Links on other hosts remain available to open,
but the extractor enforces its approved-host policy. Without a saved link,
supplier lookup can propose a datasheet link alongside metadata and use it for
electrical extraction. If extraction from a saved link fails, finds no matching
part, or needs clarification, refresh requests supplier metadata. When that
lookup proposes a different datasheet URL, the same refresh attempts electrical
extraction from the replacement once. An unchanged link is not retried.
Metadata and electrical reviews remain separate. Resolve
a pending electrical review before another extraction; reload and open Show
electrical specifications to recover the review. Accept/dismiss checks the
displayed review through the shared server approval engine, preserving stock.

Saving a datasheet link associates that document with the inventory part; no
separate identity override is needed for electrical extraction. Inventory labels
can omit shipping or package suffixes. Shared, evidenced ratings can be proposed
without renaming the part. Ratings that depend on an unresolved electrical grade
remain unknown, and clearly unrelated documents or manufacturer conflicts still
fail. A supplier-proposed link requires identity evidence until saved.

## Unnumbered stock identity

Unnumbered connectors, modules, kits and other stock outside resistor, capacitor
and inductor categories remain separate physical groups. Adding another group
creates a new record even when its category, value, package and description
agree. To increase an existing group, select its record explicitly and adjust
its quantity; in chat, identify the part ID for `add_stock`. Descriptions are
labels, not verified ordering codes. Numbered stock retains exact-code identity.

## Bulk inventory edits

In Inventory, select up to 100 rows individually or use **Select visible parts**
after filtering. Selections remain selected when hidden by a filter; the selected
count identifies hidden rows. Reloading inventory clears the selection.

Choose **Edit selected**, check only fields to change, and enter their common new
values. A checked blank field clears its value; quantity sets the same absolute
count on every selected row. **Review changes** shows normalized before/after
values for every selected part, including any changes caused by inventory
normalization. Saving dismisses pending metadata reviews on the selected rows.

**Approve changes** applies each part independently through the shared approval
engine. Successful parts remain saved when another part is stale or conflicts;
the outcome lists saved parts and per-part failures. Failed parts remain selected
so you can prepare a fresh review. Retrying the original approval replays saved
per-part receipts without reapplying successful changes. Single-part effects and
their receipts still commit together. Bulk editing requires no provider requests.

## Interrupted requests

HTTP message intake requires an `execution_id` form field before paid work.
Reuse that identity and identical input if the intake acknowledgement is lost;
conflicting reuse returns 409. Refresh old browser tabs after this upgrade. Browser message
streams only follow persisted progress; closing them does not cancel execution.
The `parts-bin-worker` container claims due PostgreSQL deliveries, advances one
submission, collection or tool stage, then releases ownership. It polls known
response IDs every thirty seconds when notifications are missing and reconciles
inbox entries and unscheduled checkpoints every fifteen seconds. No AWS resources
or broker are required for this deployment.

Before upgrading either service, take a PostgreSQL dump and protect the current
configuration and image IDs using the backup procedure below. Run the explicit,
additive schema upgrade using the candidate image and protected config mount:

```sh
python -m db.postgres.setup --config /app/config.toml
```

Then deploy both `parts-bin` and `parts-bin-worker`. The app never initializes the
schema on startup. For non-container local use, run `uv run python -m
agent_runtime.worker` alongside the API. `--once` reconciles and attempts one due
stage; `--health` checks the database worker heartbeat. Inspect bounded worker logs
and preserve the original execution ID when recovering failures:

```sh
docker compose logs --tail 100 parts-bin-worker
docker compose exec parts-bin-worker python -m agent_runtime.worker --health
```

Public installations should register the exact HTTPS URL
`https://YOUR_HOST/agent/webhooks/openai` with the installation's OpenAI project:

```sh
python -m scripts.openai_webhook --config /app/config.toml --url https://YOUR_HOST/agent/webhooks/openai
```

Run this on the host/candidate image with a writable protected configuration mount,
then restart HTTP intake. The helper saves the one-time signing secret locally;
never paste it into Git or command output. It refuses duplicate endpoints or
implicit secret rotation. If registration succeeds but local writing is interrupted,
recover the protected `config.toml.webhook.pending` file; if no secret was saved,
rotate explicitly in the OpenAI dashboard instead of creating another endpoint.
Webhook management needs project permission; if the API login lacks it, register
in the dashboard and put the secret in `[agent.openai].webhook_secret` using the
operator's protected secret procedure. Local development needs no public callback.

The webhook route verifies the original body and timestamp with the official SDK,
commits a minimal notification and wakeup before returning 204, and retrieves no
provider response in the receiver. Invalid signatures return 400, oversized bodies
413, and unavailable verification/storage 503. Callbacks do not need browser login;
the rest of the deployment retains its existing access boundary. Inbox receipts
expire after seven days; unknown project response IDs cannot start new executions.
An early callback waits for the matching saved response ID. Old callbacks cannot
advance a different model round.

Safe retrieval/stage delivery failures back off up to sixty seconds. Eight consecutive
failures park the delivery and publish `dispatch_blocked`; inspect the worker error
type, restore connectivity/configuration, then use **Resume request** (or **Retry cancellation** for cancelled work awaiting provider cleanup). Lease conflicts
defer without consuming that budget. Historical executions are enrolled only on an
explicit resume, never by reconciliation. Approvals remain parked until a validated
decision and wakeup commit together. Graceful worker stop finishes the current
bounded stage; abrupt loss is recovered after its five-minute lease expires.

Chat progress reconnects automatically after a dropped or stalled stream. The
browser reads events after its last contiguous stored sequence, removes duplicate
events, and follows active durable deliveries. Reloading first restores the selected
conversation's history, then follows any active work. It does not resubmit messages,
photos, approvals or resume commands. Only the conversation pointer is saved in
browser storage; photos and model context remain outside it.

During temporary outages the UI shows **Reconnecting to progress…** and retries
read-only observation with backoff up to ten seconds. A progress body that stays
silent for fifteen seconds is closed and recovered through replay; accepted worker
stages continue independently. Switching conversations or opening a new chat stops
that browser's observer without cancelling server work. Authentication failures,
missing conversations and malformed progress state stop automatic retries and
require sign-in or **Refresh history** after the underlying problem is fixed.

If a message acknowledgement is lost, the browser checks the original execution
identity through read-only history. A confirmed intake clears its draft and continues
observation. An unconfirmed intake retains the draft and requires checking history
before another submission. A lost approval acknowledgement reads the saved decision;
it never approves again automatically. Approval waits, blocked deliveries and
unenrolled historical executions keep explicit approval/resume controls. Background
polling for model collection is separate from these browser progress reads.

Use **Cancel request** to stop later stages, including while chat is working or
awaiting approval. Cancellation is durable and irreversible for that execution;
disconnecting from chat still only stops observation. A stage authorized before
cancellation may finish, including an inventory mutation or paid retrieval. Its
committed stock, evidence and receipts are preserved. Later tools and model
submissions are skipped, and pending approvals cannot revive cancelled work.

The worker cancels a saved background response ID through OpenAI's idempotent
cancel endpoint, discarding returned tool calls even if generation already completed.
Transient cleanup failures use the ordinary dispatch retry budget. A blocked cleanup
needs **Retry cancellation** after fixing its cause; **Resume request** is forbidden.
Refresh history to confirm a lost cancellation acknowledgement. Cancellation does
not guarantee avoidance of charges for already started work. If the submission ID
was lost, the terminal event reports `submission_uncertain`; no replacement request
is sent and provider usage needs inspection. A missing/expired response reports
`unavailable`, without claiming confirmed provider cancellation.

Before upgrading, explicitly initialize the additive cancellation table with
`python -m db.postgres.setup --config /app/config.toml` using the candidate image,
a protected configuration mount and the database network, following the backup
procedure above. Never roll back HTTP or workers to a version preceding ADR 0032
while cancellation records exist: old workers ignore the requests and can continue
paid work. Keep the database and use a compatible repair release, or rehearse an
explicit checkpoint migration on a restored copy before any older-image rollback.

Background submission keeps `store=false`. OpenAI temporarily retains background
results for roughly ten minutes; collect them promptly. `model_response_unavailable`
means the result is missing or expired and is not automatically resubmitted.
`model_submission_uncertain` means the worker could not save a submission's response
ID. Inspect provider usage before making a new request; replaying that execution
does not repeat the POST. A transient collection failure can continue with the same
execution ID and saved response ID. The application never persists photo bytes:
initial submission needs the accepted photo, while result collection does not.
Different photos require a new request identity.

Once background workers are used, drain their executions before rolling back to
a version predating [the background checkpoint contract](adr/0027-durable-intake-and-background-responses.md).
Such versions can mistake a pending submission for a fresh model call. Configuration
and data remain separate from the application image.

Compose upgrades give active requests time to finish: Uvicorn waits up to five
minutes, inside Docker's five-minute-thirty-second stop window. The extra thirty
seconds allow cancelled workers to release leases and close connections. New connections can fail
during this single-container replacement. Do not shorten deployment stop timeouts.
When upgrading an older container with the default ten-second stop window, pass
`--timeout 330` to the deployment's existing `docker compose up` command for that
first replacement; subsequent containers retain the Compose setting. Preserve the
existing production override files and infrastructure deployment procedure.

**Refresh history** reads saved events without repeating work. **Resume request**
explicitly continues the original execution after refreshing history. An active
worker can prevent resume; after abrupt process loss its lease can take five
minutes to expire. Do not resend the original message to recover a mutation.
An active lease produces a temporary busy notice with a retry bound, preserving
the request's resume controls. Refresh history first: the original worker may have
finished meanwhile. Graceful worker shutdown releases its lease after the current
stage; hard process or host loss still needs lease expiry. Process shutdown and
explicit application cancellation are separate operations.

Use the original approval controls and request ID for a waiting decision.
Duplicate delivery returns the saved outcome. A changed target or an unknown
historical approval requires a fresh proposal; conversation prose cannot replace
approval.

Resuming an uncertain background submission fails conservatively without repeating
the POST. Inspect provider usage before sending a new request. Interrupted supplier retrieval
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
provenance is available for an individual review. Use
`list_pending_specification_reviews` for electrical reviews, optionally filtering
by exact committed category, ordering code, or part ID. Follow `next_offset` with
unchanged filters and page size; restart if reviews or inventory change. Discovery
returns identities and proposed fact names; inspect `get_specifications` for a
selected part's values, conditions, evidence, and accepted facts before approval.

`context_budget_exceeded` and `model_response_incomplete` end an execution without
automatic retry. Inspect saved outcomes and stock before starting a narrower
request: completed mutations remain committed. Refresh history to recover a missed
acknowledgement; resuming a terminal execution returns the saved outcome.
Their stable codes are recorded as `agent_runtime_error`. `agent_model_request`
records text-input bytes separately from opaque reasoning bytes, tool/exchange
counts and omitted-result counts. Inspect the correlated thread fingerprint and
saved error before diagnosing a BOM failure: the text budget includes instructions
and tool schemas. `agent_execution_deferred` identifies temporary lease conflicts,
which are not failed executions. Diagnostics do not record prompts, tool values,
photo bytes or reasoning content.

Supplied-PDF extraction uses selected source excerpts. If these cannot establish
identity or evidence, provide a shorter datasheet for the exact ordering variant.
Missing evidence in selected excerpts does not establish that the part is absent
from the source. A `no_match` result requires a cited passage identifying a
conflicting device, family, or manufacturer; inspect that evidence before
selecting a different source. A missing exact code alone requires clarification.
Manufacturer downloads identify Parts Bin in the
request headers. Download, parsing and model excerpt limits remain independent;
see [the extraction implementation](../ingestion/supplied_source.py) for current
bounds. An HTTP or parsing failure stops before a paid extraction and must not be
reported as no matching part. No automatic retry substitutes a different source.

## Backup and recovery

Inventory, evidence, reviews, approvals, operation receipts, execution checkpoints,
and conversations share one PostgreSQL database. Back up the whole database;
receipts and checkpoints protect against repeated effects. Keep protected
configuration separately and an off-host copy of both. Supplier caches are
disposable; photos are ephemeral.

Use PostgreSQL client tools matching the server. Supply authentication through a
protected `PGPASSFILE`, not a command-line password:

```sh
umask 077
pg_dump --host POSTGRES_HOST --username parts_bin --dbname parts_bin \
  --format custom --file /protected/backups/parts-bin.dump
```

Rehearse restoration into a separate empty database owned by the app login:

```sh
pg_restore --host POSTGRES_HOST --username parts_bin --dbname parts_bin_restore \
  --exit-on-error /protected/backups/parts-bin.dump
```

Point an isolated app at the restored database. Check inventory counts and stock,
accepted evidence, pending reviews, conversation ordering, saved receipts and a
read/write workflow before recovery. Keep the original installation intact.
Choose retention against acceptable data loss. Before upgrading, retain a backup
and the previous image. Code rollback and data restoration are separate: restoring
a snapshot loses subsequent writes, and an older image may require an older schema.

## Local database containers

[Local setup](../README.md#local-setup) uses `compose.postgres.yaml`. Start or
stop the persistent development server with:

```sh
docker compose -f compose.postgres.yaml up -d --wait postgres
docker compose -f compose.postgres.yaml stop postgres
```

The development volume survives container removal. Do not use `down --volumes`
unless deliberately discarding local inventory. The optional `test` profile uses
a different port/login and tmpfs instead of a persistent volume. Remove only its
container after tests using the README command. Both ports bind to loopback.
Password files in ignored `data/` must remain private. Initialization passwords
apply only to empty databases; changing a file does not rotate an existing login.
The [official image documentation](https://hub.docker.com/_/postgres) describes
initialization and file-based secrets.

## SQLite migration

SQLite is a read-only migration source, not a runtime option. Rehearse on snapshots
before cutover. Use SQLite's online backup facility to capture WAL contents; if
inventory and conversations are separate files, stop writers before taking the
coordinated snapshots. Preserve originals and protected configuration:

```sh
umask 077
sqlite3 /old/data/parts.db ".backup '/protected/parts-snapshot.db'"
sqlite3 /protected/parts-snapshot.db 'PRAGMA integrity_check;'
uv run python -m db.postgres.migrate --config /protected/postgres-config.toml \
  --inventory /protected/parts-snapshot.db
```

Add `--conversations /protected/conversations-snapshot.db` only for a separate
history database. The importer initializes the schema and requires an empty
authoritative target. It verifies every imported row before commit, retaining
identities, deleted-ID allocation history, evidence, event ordering, retired-provider
history, approvals, receipts and checkpoints. Disposable supplier caches are not
copied. Repeating the same import returns its manifest without overwriting newer
writes. Verify a restored copy against the original snapshots explicitly:

```sh
uv run python -m db.postgres.migrate --config /protected/restored-config.toml \
  --inventory /protected/parts-snapshot.db --verify
```

For cutover, drain and stop the old app, capture final snapshots, import into an
empty dedicated database, switch the protected app configuration to PostgreSQL,
and start the new image. Check search and history before accepting writes. Retain
the SQLite originals as recovery artifacts. After PostgreSQL receives new writes,
returning to the old SQLite files loses those writes; use PostgreSQL recovery or a
compatible application rollback instead. This importer targets PostgreSQL. A live
DSQL data copy needs a separate limit-aware procedure and rehearsal.

## DSQL operator experiment

The operator provisions the AWS cluster. The DSQL adapter shares domain rules and
SQL with PostgreSQL, but IAM authentication, concurrency, service limits and
DDL need live verification before cloud deployment. Initialize using an authorized
AWS identity (no static database password):

```sh
uv run python -m db.dsql.setup --endpoint CLUSTER_ENDPOINT --region AWS_REGION --user admin
```

Compose injected services with `DSQLPartsBinRepository(DSQLConfig(...))`; the local
factory intentionally selects PostgreSQL. Use the cluster's normal app login for
runtime access. Tokens are generated per connection and TLS verifies the endpoint.
Run the optional adapter scenarios against an isolated schema using the environment
settings documented in [the test fixture](../tests/db/dsql/conftest.py). They create
and remove test tables; use a dedicated test cluster/login, never production stock.
Do not infer live DSQL compatibility from PostgreSQL test results. Cloud auth,
Terraform, orchestration and recovery remain in [TODO](../TODO.md).

## Electrical fact review

Chat exposes supported fields through `get_specification_contract`. Assertions
can be proposed and approved in chat but remain labeled as user assertions.
For sourced ratings, provide an exact inventory target and an approved manufacturer
PDF URL in chat. `ingest_datasheet` retrieves the source and stages supported
electrical facts. It uses the configured OpenAI model and can incur a paid call.
Stock without an exact part identity needs clarification first. A standalone
device title can establish an unsuffixed identity when the source assigns the
ratings to that device; a family covering different electrical grades still
needs clarification. Supplied ordering suffixes must remain exact. Inspect every
proposed value, qualifier, condition and source passage; authentic quotations do
not by themselves prove that the model interpreted a table correctly.

Use `bjt` or `mosfet` for identified transistors. For existing generic `transistor`
records, establish the subtype from an exact identity and request an explicit
category correction before electrical extraction. Inventory is not automatically
relabeled. Semiconductor facts need the test conditions returned by the contract;
previously accepted facts missing required conditions remain stored but become
incomplete for confirmed matching. Inspect the source and stage a qualified
replacement through review. MOSFET threshold voltage does not establish fully-on
gate drive; distinguish diode continuous, repetitive peak and surge ratings.

For operator-inspected evidence, prepare facts using
[the validated contract](../domain/specifications.py).

Stage an inspected JSON array on an isolated inventory copy first:

```sh
uv run python -m ingestion.review_specifications PART_ID /path/to/candidate.json \
  --config /path/to/isolated-postgres-config.toml
```

This stages a review; it does not retrieve or authenticate evidence. Inspect with
`get_specifications`, then request `apply_specification_review` for approval or
`reject_specification_review` to discard it. Pending facts do not confirm electrical
requirements. Approval preserves evidence and quantity; user assertions remain
assertions. `lookup_part_specs` retrieves base metadata. See
[the specification decisions](adr/0007-reviewed-electrical-facts.md).

## Supplied-source retries

The supplied-source command stages metadata from a supplied manufacturer PDF:

```sh
uv run python -m ingestion.enrich_source PART_ID SOURCE_URL --model MODEL
```

Add `--electrical` to stage electrical facts instead of metadata. The chat tool
uses the same extraction and fact review path. Approved hosts are listed in
[the source policy](../ingestion/supplied_source.py); unsupported hosts and
oversized or unparsable documents fail without a proposal. Image-only documents
return a request for visual review without a text-only model call. A family
datasheet that does not establish the exact ordering variant needs clarification.
The extractor leaves unsupported fields unknown and retains only bounded evidence,
not the PDF. Resolve an existing electrical review before another extraction.
Chat shows an extraction confidence score based on the fraction of supported
fields extracted with validated citations and minimum qualifiers. It is a coverage
heuristic, not a calibrated probability of correctness or proof that qualifiers are complete.
Missing fields and omitted context are explained. Use the relevant-page links to
open the manufacturer PDF at exact-code, ratings and measurement pages; PDF
viewers that ignore page fragments can be navigated manually. Scanned documents
need visual inspection or a text/OCR version; automatic OCR is not performed.
Detected table cells retain their bounds so merged cells can be interpreted
without filling blanks. Review uncertain associations in the original viewer.

Electrical evidence can include several source passages; review every cited row,
heading and footnote before approving. Electrical extraction considers each
supported field explicitly and cites identity evidence by supplied passage ID.
Null fields remain unknown; an explicit decision and authentic source passage
do not establish complete interpretation. Metadata-only extraction continues
to use short source quotations. Capacitor capacitance requires explicit
measurement frequency and temperature;
its rated voltage requires AC/DC and rating temperature before a match can be
confirmed. Incomplete facts may remain in a review or accepted history, but
cannot confirm those requirements. Matching also depends on stored conditions.
Equivalent explicit Celsius and humidity intervals or center ± tolerance
compare by equal endpoints while the original conditions and passages remain
stored. Resistive and resistive-load spellings of load type compare alike.
Named electrical conditions compare scalars, ranges and center ± tolerance by
equal SI endpoints. For example, `supply_voltage` values `5 to 36 V`
and `5000 mV to 0.036 kV` match. A range with units only after the upper endpoint
uses those units for both endpoints. For test-point, typical, rated and stress
facts, different endpoints and a scalar within a range remain distinct. Trailing
qualifiers and dual-rail annotations also remain distinct. This
also applies to named current, frequency, duration and resistance conditions.
Specification values and requirements also accept nonnegative intervals, such as
`1 to 2 V` or `1.5 V ± 0.5 V`. Equality checks both endpoints. At least / at most
checks every possible stored value against the requested bound; uncertain overlap
is incomplete. A requested interval uses its upper bound for at least and lower
bound for at most. To check a `4.5 to 5.5 V` supply, require the operating minimum
at most that interval and the operating maximum at least that interval, with
complete source-covered conditions. Accepted operating minimum/maximum facts
can cover a scalar or narrower interval inside each documented condition range;
for example, ambient `25 °C` is covered by `-40 to 85 °C`. All condition names
and nonnumeric qualifiers remain required on both sides. Outside-range queries
stay incomplete. Other fact bases require equal conditions. Stress limits cannot
replace operating endpoints.
Nominal resistor, capacitor and inductor identity facts remain scalar; keep their
tolerance separate. Original values, source passages and review history remain stored.

Condition names remain distinct. Unknown names and values compare literally; no derating or
room-temperature inference is performed. Reject proposals that omit a source's
applicable temperature, test environment,
min/max bound, load pairing or reference-only qualifier. The
[disposable live runner](../evaluation/README.md#supplied-source-extraction-exercise)
evaluates source interpretation separately from approval mechanics.

Its disposable cache shares the local inventory database. Never delete that
database to clear a cache. A failed or interrupted attempt blocks retries,
including `--refresh`, for five minutes from its start. Afterward, retry explicitly;
failure is not a no-match result.

`--refresh` downloads again; an unchanged hash can reuse the extraction without a
model call. A failed refresh clears the prior result, so retry may incur another
charge. Cache expiry does not affect staged or accepted evidence.
An interrupted chat extraction requires an explicit new user turn to retry;
resuming its execution does not repeat an uncertain paid stage.

## BOM availability and electrical search

Paste a BOM and ask for stock availability. The bot submits parsed rows to the
whole-list inventory check; deduplicating beforehand is optional. The server
combines identical requirements and retains original row indices. Unknown
quantities and overlapping stock need clarification before claiming coverage.
The check does not reserve stock. Lists over the input bound need separate
submissions; do not assume those checks allocate stock across submissions.
When a submitted row lacks both a part number and a category/value identity,
the check rejects the whole request and returns every affected zero-based
`input_indices`. These rows need clarification, not a missing-stock claim.
Supply the missing identities before checking the whole list again, or check
only its identifiable rows while explicitly accounting for unresolved rows.

Use the category specification contract to discover available regulator and LED
fields before searching accepted facts. Specify the test conditions when asking
for voltage, current or optical performance. Input operating ranges require both
endpoints; absolute maxima do not establish operation. Fixed and adjustable
outputs differ. Unmarked LEDs need identity clarification before source-backed
extraction. New fields do not populate existing stock automatically: supply a
manufacturer datasheet, inspect the proposal and approve it through review.


## Flexible inventory searches

Search results distinguish confirmed matches, partial matches, and candidates
needing evidence or matching conditions. A partial match does not meet all the
requested requirements. Each known electrical failure includes the requested
comparison/value, the actual accepted fact, conditions and its source passages.
Unknown facts and mismatched qualifiers remain unconfirmed, even when a candidate
also has known failures. Review every difference before choosing which requirement
to relax; a partial match is not a claim of interchangeability or safe operation.

An explicit resistor, capacitor or inductor category plus nominal value filter
also returns nearby numeric values. The electrical filter panel has an optional
nominal value field. These alternatives show requested versus committed values;
unknown or incompatible value notation is not given an invented proximity.
Package, profile, full ordering code and minimum quantity remain exact filters.
BOM availability checks still use exact requirements and do not count alternatives.

Confirmed and incomplete records retain ID order. Partial alternatives rank by
most confirmed electrical requirements, fewer unresolved requirements, fewer
known mismatches, then smaller normalized numeric shortfall and ID. Categorical
mismatches have no inferred numeric similarity. This ordering helps review; it is
not a confidence or suitability score. There is no assumed acceptable tolerance.

Follow `next_offset` with the same query to retrieve all result groups. Each
group is independently bounded by `limit`; counts include the whole group, not
only the current page. Restart pagination if stock or accepted facts change.
