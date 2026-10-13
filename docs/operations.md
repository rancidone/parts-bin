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

The optional `datasheet_url` column is added on application startup. Existing
rows receive null; IDs, quantities, evidence, reviews and conversation history
remain stored. Rehearse startup on a SQLite backup before deploying. After an
upgrade, approvals bound to the previous complete inventory shape can require
a fresh approval; replay of already committed mutation outcomes remains intact.
For rollback, the previous application can leave the extra nullable column in
place. Restore a backup only when explicitly recovering data.

## Unnumbered stock identity

Unnumbered connectors, modules, kits and other stock outside resistor, capacitor
and inductor categories remain separate physical groups. Adding another group
creates a new record even when its category, value, package and description
agree. To increase an existing group, select its record explicitly and adjust
its quantity; in chat, identify the part ID for `add_stock`. Descriptions are
labels, not verified ordering codes. Numbered stock retains exact-code identity.

Startup replaces the legacy unnumbered-stock uniqueness index with one limited
to resistor, capacitor and inductor categories. Rehearse startup on an online
SQLite backup first; the migration changes no rows or historical records. Keep
the pre-upgrade backup. After distinct unnumbered groups share category/value/
package, an older application can refuse startup or merge their stock incorrectly.
Use the current version to preserve those groups. If recovery requires an older
version, restore the pre-upgrade backup deliberately, accounting for subsequent
writes that restoration would lose.

## Bulk inventory edits

In Inventory, select up to 100 rows individually or use **Select visible parts**
after filtering. Selections remain selected when hidden by a filter; the selected
count identifies hidden rows. Reloading inventory clears the selection.

Choose **Edit selected**, check only fields to change, and enter their common new
values. A checked blank field clears its value; quantity sets the same absolute
count on every selected row. **Review changes** shows normalized before/after
values for every selected part, including any changes caused by inventory
normalization. Saving dismisses pending metadata reviews on the selected rows.

**Approve changes** applies the whole batch through the shared approval engine.
An invalid identity, duplicate, or changed record prevents the entire batch.
If stock or pending evidence changes during review, prepare a fresh review before
approving. Retrying the same approval replays its saved result. Bulk editing
requires no model or supplier requests.

## Interrupted requests

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
finished meanwhile. Graceful forced cancellation releases its lease immediately;
hard process or host loss still needs lease expiry. Cancellation retains committed
stock and checkpoints, rather than automatically resending work.

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
