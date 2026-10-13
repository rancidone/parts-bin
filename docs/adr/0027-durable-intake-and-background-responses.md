# 0027: Persist intake and separate model submission from collection

Status: Accepted

## Context

A bounded model stage still occupies a worker until the provider answers. A
serverless host needs to submit a request and exit, then collect the result in
another invocation. Dispatch retries also need a stable application identity
before any paid work begins.

OpenAI Responses supports background submission and retrieval by response ID.
With `store=false`, background response data is temporarily retained for roughly
ten minutes to support polling. See the official [background guide](https://developers.openai.com/api/docs/guides/background).
The application must not assume unlimited result retention or an undocumented
provider submission-idempotency guarantee.

## Decision

Add `AgentGateway.prepare` to persist intake under a caller-supplied execution ID
without acquiring a worker lease or invoking the provider. Bind that identity to
its thread, original text, and a digest of the supplied photo and media type.
Repeating identical intake returns the same ID without resetting progress or
ownership; conflicting reuse is rejected. Preserve the initial conversation
context. Photos remain outside application persistence and durable messages.

Expose `advance_background` as an explicit worker entry point on the same OpenAI
runtime and transport. It uses the existing tool, approval, operation-ledger and
lease rules. Each invocation performs one submission, one result retrieval, or
one tool stage. Persist submission intent before the POST, then save the returned
response ID before returning `waiting_model` and releasing ownership. Submission
uses `background=true` with the existing `store=false` policy. There is no polling
loop or new provider/backend selector.

A later invocation retrieves that exact response ID. Queued and in-progress
responses return `waiting_model`; only completed responses enter the normal
model-output checkpoint. Save native response items before any tool invocation.
Terminal unsuccessful responses, expired results, unknown statuses and identity
mismatches fail without consuming partial calls or resubmitting the model request.
Transient retrieval failures retain the response ID for explicit continuation.

An interruption after submission intent but before response-ID persistence has
an uncertain paid outcome. End it with `model_submission_uncertain` on recovery
rather than submitting again. This is deliberately conservative even if the
network failure happened before the provider accepted the request. Preserve
committed stock and require provider-usage inspection before a new request.

Keep today's local HTTP flow on foreground completion. Do not enable automatic
background dispatch or change provider retention settings as part of this boundary.
The new entry point supplies no scheduler, public callback endpoint, or AWS
infrastructure.

## Consequences

Workers can release compute while a provider generates, and worker replacement
can retrieve completed results without issuing another generation request.
Durable dispatch still needs continuation scheduling, cancellation, lost delivery
reconciliation, and authenticated progress delivery. A polling adapter must stay
within the provider's temporary retention window; prolonged outages can lose the
result even when the application knows its ID. This tradeoff must be measured
before adopting background generation in production workflows.

Intake stores a digest, not a photo. A worker needs the original photo only to
submit initial analysis; losing it before submission requires resubmission. A
replacement photo must match accepted intake or use a new request identity.
After submission, collection needs only the saved response ID.

Checkpoint JSON gains optional fields without a database schema migration;
existing executions continue using their original context. Older application
versions do not understand in-flight background submissions. Once these workers
are used, drain them or retain a compatible version for recovery rather than
rolling back and accidentally submitting the paid stage again.
