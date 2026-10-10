# Storage and retention decisions

## Keep the installation small

Preserve inventory, accepted provenance, conversations, and approval state across
execution replacement. Preserve execution checkpoints and mutation outcome IDs
with them: retry protection depends on those authoritative records. Retention of
mutation outcomes requires an explicit retry horizon and recovery decision;
completed-job diagnostic cleanup must not remove them.
Treat enrichment caches and completed-job diagnostics as
disposable. Their loss may cause another lookup, but must not lose stock or repeat
an inventory mutation.

Use the application's durable storage for compact cache and job records rather
than introducing a dedicated cache service initially. The cloud database is
still an open choice; this decision does not select DynamoDB or prescribe a schema.

## Photos and retrieved documents

Photos live in browser/session memory and are transmitted only for processing.
Do not persist image bytes or encoded images in conversation records, logs, or
object storage. A worker holds the image temporarily while processing it. Do not
put image payloads in durable queue messages. Retrying after the session is gone
requires resubmission. Provider-side retention is a separate concern to verify
against the chosen API configuration.

Do not retain downloaded enrichment pages or PDFs. Keep the source URL, retrieval
time, a hash of the retrieved content, and bounded supporting excerpts with page
or section references. Retain these with accepted facts so cache expiry cannot
erase their provenance. A hash identifies content; it cannot reconstruct it.

This accepts that a publisher can change or remove a document and the exact
original may become unavailable. Saved passages explain the basis of a proposal,
but do not establish full-document reproducibility. Object storage is not needed
for photos or enrichment documents under this policy. Frontend hosting and backup
storage remain independent decisions.

## Cache and paid work

Cache discovery by exact identity and discovery-policy version. Cache validated
extraction by exact identity, document hash, and extraction/schema version. Keep
cache results independent from each inventory entry's approval. Preserve meaningful
part suffixes and unknown manufacturer identity; never merge ambiguous parts just
to improve cache hits.

Deduplicate in-flight work for the same identity and policy. Adding stock does not
itself invalidate enrichment. Reuse sufficient existing evidence, skip discovery
for a supplied source, and support explicit refresh. Changes to extraction policy
can invalidate cache reuse without invalidating historical accepted provenance.

Bound calls, content size, retries, and tokens in application code. Persist paid
stage results and attempt accounting to reduce repeated work after interruption.
Check a shared spending allowance before each paid stage; queue concurrency only
limits spending rate, not total spending. Provider timeout does not prove that a
request was unprocessed or unbilled.

## Starting retention defaults

These are proposed tuning defaults to evaluate during implementation, rather than
claims about current behavior:

| Data | Starting policy | Reason |
| --- | --- | --- |
| Inventory and accepted provenance | Keep until explicitly deleted | Authoritative user data |
| Conversations | Preserve existing history; user-controlled deletion | Avoid silently losing existing data |
| Pending approvals and reviews | Keep until resolved or explicitly cancelled | Cleanup must not authorize or discard work silently |
| Successful enrichment cache | Revalidate after 90 days of freshness | Specifications change less often than stock |
| Definitive no-match result | Reuse for at most 24 hours | Search coverage can change |
| Retrieval failure | Brief retry backoff, initially 5 minutes | Failure is not absence of a part |
| Completed-job diagnostics | Delete after 7 days | Debug recent work without unbounded growth |
| Operational logs | Delete after 14 days | Retain useful recent failures |

Cache freshness and physical cleanup are distinct: an expired entry must not be
used as fresh even if asynchronous deletion has not run. Superseded cache records
can be removed without deleting accepted provenance or unresolved proposals.
Keep logs compact: IDs, outcomes, timing, usage, and sanitized errors rather than
images, full documents, credentials, or full provider payloads.

Backup retention, recovery objectives, and automatic cleanup of abandoned work
still need decisions. Backups require an independent recovery path; disposable
cache does not substitute for one.
