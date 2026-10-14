# 0030: Dispatch durable stages through PostgreSQL and signed OpenAI notifications

Status: Accepted

## Context

Our current deployment already owns PostgreSQL and a Docker host. Background
Responses and fenced execution checkpoints let workers exit while the model
runs, but intake, continuation and crash recovery need durable scheduling.
Adding a cloud broker before the AWS experiment would create another operational
boundary without changing the application's requirements.

OpenAI notifications can repeat, arrive before a submitted response ID is saved,
or be delayed beyond the temporary result retention window. See the official
[webhook guide](https://developers.openai.com/api/docs/guides/webhooks) and
[background guide](https://developers.openai.com/api/docs/guides/background).

## Decision

Use a PostgreSQL dispatch row per explicitly enrolled execution as the current
outbox and queue. Accept intake and its delivery in one transaction. Save every
checkpoint with its next delivery in the same transaction; terminal and approval
checkpoints remain deliverable until event publication is acknowledged. Preserve
existing execution fences, operation receipts, input identity and approval rules.
Do not automatically adopt historical executions.

A separate container advances bounded stages. Delivery tokens fence stale
acknowledgements; a wake version prevents an acknowledgement from swallowing a
callback or approval wake that arrived during the stage. Retry safe collection
and checkpoint recovery with bounded backoff, parking repeated failures for
explicit resume. Submission intent remains fenced before the paid POST: an
uncertain submission fails on recovery rather than repeating generation.
Interrupted paid retrieval stages keep their existing conservative policy.

Verify terminal OpenAI notifications using the official SDK against the original
body and timestamp. Persist only delivery ID, response ID, event type and receipt
time in an inbox; commit the inbox and matching wake before acknowledging. Never
execute notification contents as tool calls. A notification for an unknown response
cannot create work. Saving the corresponding response ID also consumes any early
notification. Periodic collection and reconciliation recover missed callbacks,
expired worker leases and publication gaps. Expire inbox receipts after seven days.

HTTP chat streams read persisted progress independently of execution. Browser
reconnection is defined by [0031](0031-read-only-chat-progress-reconnect.md). Browser
submission includes a stable execution identity. Photos remain ephemeral: intake
reserves and submits the initial stage while bytes exist in HTTP memory; subsequent
workers only need the provider ID and derived context. Losing an unsubmitted photo
requires a new photo request. Browser disconnect and deployment are not cancellation.
An explicit cancel operation remains a separate decision.

Keep `store=false`, the installation's configured model, and the current access
boundary. Local development can use polling without a public endpoint. Public
installations register a project webhook and protect its signing secret alongside
other host-local configuration. Schema installation remains an explicit upgrade
step, with backup before deploying both HTTP and worker services.

## Consequences

The current host exercises the same durable handoffs needed by future serverless
workers without new AWS resources. It still runs a small persistent polling process;
this decision does not select the future AWS scheduler or broker. Database polling
and the global write revision are suitable for the current single-owner installation;
measure them before scaling concurrency.

Worker outages longer than OpenAI's roughly ten-minute result window can lose a
result even when its ID is saved. Webhook retry longevity does not extend response
retention. Missing/expired results fail without replaying paid generation. Reconcile
and collect promptly, monitor the worker heartbeat and error logs, and measure
representative photo/search/enrichment latency before claiming production-scale
cost or reliability. Roll back only to a version that understands enrolled background
executions, or drain them first; restoring a dump does not restore expired provider data.
