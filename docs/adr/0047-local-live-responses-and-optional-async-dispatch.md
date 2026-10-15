# Live Responses for local installs; optional async model dispatch

## Context

Production traces showed a fixed thirty-second collection delay for every model
round with background Responses enabled and no webhook configured. Single-item
lookups took about 64 seconds and four-round bulk additions about 128–130 seconds,
while their inventory tools took milliseconds. Even a clarification without tools
took about 32 seconds. This is scheduling delay, not evidence that database or
inventory operations need more compute. The operator requested live waiting as
the local default while retaining separate submission and collection for async
workers.

## Decision

Default `[agent].background_responses` to false. Durable intake and the existing
worker remain independent of browser connections. Each claimed model stage waits
on the normal live Responses request; subsequent ready stages are immediately
eligible. This uses the existing transport and domain tools, with no provider
selector or UI setting.

Allow async deployments to opt into `background_responses = true`. Keep the
existing separate submission/collection stages, response IDs, callbacks and
bounded polling behavior. A mode change never resubmits an already accepted
background request: retrieve its saved response ID first.

Fence live submissions too: persist submission intent before the model POST and
checkpoint returned model output before invoking any tool. If a request is
interrupted before output is saved, fail conservatively with
`model_submission_uncertain` on recovery. Never repeat an uncertain paid request
or apply partially received tool output. Existing leases, cancellation boundaries,
server-owned approvals and atomic mutation receipts apply in both modes.

This supersedes the default background model-delivery choice in ADR 0030 and the
production-default aspect of ADR 0027. Their durable intake, checkpoints, outbox,
webhook authentication and recovery rules remain applicable.

## Consequences

Local requests incur provider generation time and ordinary stage overhead without
a compulsory thirty-second poll per model round. A live call occupies a worker
until it completes or reaches its HTTP timeout; short-lived hosts can explicitly
select async mode. Closing a browser still does not cancel accepted execution.
The operator must select the same setting for HTTP intake and worker processes,
including the initial photo stage. No database schema migration is required.
