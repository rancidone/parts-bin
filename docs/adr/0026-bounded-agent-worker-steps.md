# 0026: Advance agent executions at durable stage boundaries

Status: Accepted

## Context

The agent checkpoints model output and tool results, but its host entry point
runs the whole loop until completion or approval. A future serverless worker
needs to relinquish work between stages without treating that handoff as a
failure or requiring the original process to survive.

AWS Lambda event functions have a bounded invocation lifetime, and delivery
can repeat. See the official [timeout guidance](https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html)
and [retry guidance](https://docs.aws.amazon.com/lambda/latest/dg/invocation-retries.html).
These constraints motivate a worker boundary; they do not select Lambda or an
orchestration service.

## Decision

Expose `AgentGateway.advance` for worker hosts. It uses the same runtime loop as
the local HTTP host but checkpoints at most one model response or completed tool
call per invocation. A model response is saved before tools run; each tool result
and cursor are saved before the worker returns. Release the execution lease on
return, including approval waits and failures.

Return `ready` with the original execution ID when another stage remains.
Continue that ID in a newly assembled worker. Approval waits, terminal outcomes,
operation IDs, approval validation, and conversation identity retain their existing
semantics. The global model-round limit survives worker replacement. Intermediate
handoffs do not emit user-visible completion events; hosts read durable conversation
events independently. Propagate worker exceptions and lease conflicts to the host
so it can distinguish interrupted work from a completed failure.

Keep the local HTTP host's existing run-until-pause behavior. Photos stay ephemeral:
after the initial model response is checkpointed, subsequent workers need no image.
Interruption before that checkpoint still requires resubmission. Never automatically
repeat an interrupted supplier or datasheet stage.

## Consequences

Workers can be replaced between model and tool stages without reconstructing
business logic or replaying committed stock effects. A step bounds stage count,
not wall time: one model/tool call can still exceed a host deadline, and adding a
part can include its existing enrichment operation. Unsaved model responses remain
uncertain and may incur another charge on explicit continuation.

This boundary supplies neither durable intake nor scheduling. A future dispatcher
must persist a stable request/execution identity before starting work, reconcile
lost acknowledgements, and decide continuation and cancellation policy. Blindly
retrying a new `advance` call without an execution ID can create a new request.
Cloud persistence, authenticated progress delivery, service selection, and recovery
remain open work in [TODO](../../TODO.md).
