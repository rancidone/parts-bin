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
an HTTPS link; automatic extraction still requires an exact ordering code and
a supported electrical category. Links on other hosts remain available to open,
but the extractor enforces its approved-host policy. Without a saved link,
supplier lookup can propose a datasheet link alongside metadata and use it for
electrical extraction. Metadata and electrical reviews remain separate. Resolve
a pending electrical review before another extraction; reload and open Show
electrical specifications to recover the review. Accept/dismiss checks the
displayed review through the shared server approval engine, preserving stock.

The optional `datasheet_url` column is added on application startup. Existing
rows receive null; IDs, quantities, evidence, reviews and conversation history
remain stored. Rehearse startup on a SQLite backup before deploying. After an
upgrade, approvals bound to the previous complete inventory shape can require
a fresh approval; replay of already committed mutation outcomes remains intact.
For rollback, the previous application can leave the extra nullable column in
place. Restore a backup only when explicitly recovering data.

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
For sourced ratings, provide an exact inventory target and an approved manufacturer
PDF URL in chat. `ingest_datasheet` retrieves the source and stages supported
electrical facts. It uses the configured OpenAI model and can incur a paid call.
Stock without an exact ordering code needs clarification first. Inspect every
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
  --database /path/to/isolated-parts.db
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
heading and footnote before approving. Capacitor capacitance requires explicit
measurement frequency and temperature;
its rated voltage requires AC/DC and rating temperature before a match can be
confirmed. Incomplete facts may remain in a review or accepted history, but
cannot confirm those requirements. Matching also depends on stored conditions.
Equivalent explicit Celsius and humidity intervals or center ± tolerance
compare by equal endpoints while the original conditions and passages remain
stored. Resistive and resistive-load spellings of load type compare alike. Other
condition names and values compare literally; no derating or
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
