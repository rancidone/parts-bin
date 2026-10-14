# 0032: Cancel executions at durable stage boundaries

Status: Accepted

## Context

Durable workers outlive browsers. Closing a stream cannot express a user decision
to stop work. Cancellation must preserve committed inventory and survive concurrent
checkpoints, worker replacement and uncertain provider acknowledgements.

## Decision

Add a durable cancellation request keyed by execution identity, separate from its
checkpoint. Commit the request, deduplicated conversation event and dispatch wake
in one short transaction. Repeated cancellation is safe and can explicitly recover
blocked cleanup; terminal executions retain their existing outcome. Reject unknown
identities and preserve retired-provider read-only behavior.

Serialize stage authorization and cancellation intake through the existing database
write boundary. Release the transaction before external I/O. A stage authorized
first may finish and checkpoint its result; cancellation prevents authorization of
later stages. Do not revoke an active lease or interrupt a mutation transaction.
Recover committed operation receipts for visible progress without reexecuting tools.
Keep pending approvals as history, but reject decisions and resume after cancellation.

For a known background response ID, call OpenAI's idempotent cancellation endpoint.
Discard all response output, including tools from an already completed response.
Retry transient cleanup failures through durable dispatch; publication gaps replay
the saved terminal outcome without another provider call. Preserve the response ID
for audit and recovery. Missing/expired responses settle locally with an explicit
unavailable outcome. An uncertain submission without an ID settles locally with
submission uncertainty and is never repeated.

Expose a cancel control independently of the original POST/progress observation.
Confirm lost acknowledgements through stored event cursors. Show requested versus
terminal cancellation, preserve committed-result displays and stop approval/resume
controls. Browser disconnects retain the read-only semantics of ADR 0031.

## Consequences

No broker or infrastructure service is needed. A schema setup step adds the request
table. Cancellation can take the duration of an authorized stage plus provider
cleanup; a dead worker can delay it until lease expiry. Already started generation
or retrieval can incur charges. Unknown response identities prevent confirmation
of provider cancellation, but never justify a fresh submission.

Older workers do not honor cancellation requests. Rollback must keep compatible
workers or explicitly migrate checkpoints on a restored copy; leaving the additive
table in place alone does not make an older image safe.

Provider behavior follows [official OpenAI background-mode documentation](https://developers.openai.com/api/docs/guides/background).
