# 0031: Reconnect chat progress through read-only event cursors

Status: Accepted

## Context

Durable dispatch lets workers continue after a browser connection disappears.
The chat client still treats a lost progress stream as an interrupted request,
requiring manual refresh and sometimes explicit resume. Reconnecting must observe
already accepted work without repeating model generation, mutations or approvals.
Events have durable per-conversation sequence numbers, but a POST stream can omit
another execution's events. Completion publication can also lag its checkpoint.

## Decision

Keep intake and execution unchanged. Extend the existing finite event replay GET
with read-only delivery activity and optional intake-identity confirmation. Filter
replay by the supplied cursor in SQL. Read delivery state and visible events in
one read-only transaction, sharing the installation database across conversations
and executions. A settled delivery already published its final event, while an
unsettled terminal or approval checkpoint remains observable until publication is acknowledged.
The adapter neither creates a runtime nor claims, wakes or schedules an execution.

After stream loss or an idle timeout, read progress from the last contiguous stored
sequence. Deduplicate by conversation and sequence, preserving gaps for replay.
Confirm a lost intake acknowledgement against its original execution ID; never
retry its POST automatically. Retain unacknowledged drafts. Read a lost approval
acknowledgement from saved decisions without submitting another approval.

Follow active enrolled deliveries using finite GETs. Retry temporary network and
server failures with bounded exponential backoff; stop for authorization failures,
missing conversations or invalid event/state contracts. Stop observing when durable
work settles, blocks or awaits approval. Keep explicit recovery controls for those
states and for unenrolled historical executions. A reload restores full history
before continuing from its recovered cursor. Retired providers remain read-only.

Keep browser storage limited to its existing conversation pointer. Do not persist
photos, messages or model context there. Abort observers on conversation changes
and component cleanup, ignoring late responses from superseded observers. A
browser disconnect, idle timeout or navigation never cancels server-side work.
Show working/reconnecting state independently of mutation submission, so users
can change conversations while a background delivery is being observed.

## Consequences

Network recovery needs no new broker, schema migration, provider call or execution
retry. The current single-owner host uses one-second finite replay while active,
backing off to ten seconds during outages. Inactive histories do not poll. This
retains the installation's existing HTTP access boundary; validate authentication,
load and connection behavior again for the chosen AWS ingress.

An unknown intake identity does not prove that a delayed request can never arrive;
retain its draft and require checking history before another submission. Prolonged
provider outages can still expire model results under the existing retention policy.
Progress observation cannot repair blocked execution or recover lost image bytes.
Those remain explicit operator/user actions rather than reasons to restart paid work.
