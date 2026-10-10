# Cloud hosting decisions

## Direction

Target AWS first, using Terraform for reproducible infrastructure that an operator
can deploy into their own account. Favor serverless execution while preserving
local-first deployment and existing data. Use the OpenAI Responses API as the
single agent interface in both local and cloud operation.

The motivation is to avoid an always-managed application VM for an intermittently
used inventory workload and to learn cloud identity, durable state, and operations.
Serverless shifts operational responsibility; it does not eliminate it or guarantee
a lower bill. Database, retrieval, model, logging, and networking costs matter too.

## Alternatives and consequences

A single VM with Docker and a persistent SQLite disk is a simpler port of a local
application, but carries host maintenance and fixed-capacity responsibilities.
The serverless direction accepts application and persistence work to reduce that
host-management burden. This is a direction to validate, not a selected Lambda
or database topology.

Durable inventory, conversations, approvals, and interrupted work must survive
execution replacement. Removing a disposable supplier catalog does not resolve
those requirements. Select cloud persistence on transaction semantics, recovery,
connection behavior, and cost. Preserve local storage support and provide an
explicit data migration path; do not make the domain depend on cloud APIs.

Validate streamed agent responses, timeouts, cancellation, retries, and concurrent
requests before committing to a compute/ingress design. Keep provider limits and
prices in the official references used at decision time rather than copied
permanently into this document.

## AI workflow direction

Build the application around a bounded, tool-using agent inside durable workflows.
The model interprets intent and proposes operations; the typed domain service
owns validation and inventory effects. Separate identity resolution, evidence
retrieval, structured extraction, validation, and review so a failed stage can be
understood and retried without repeating a committed mutation. Structured output
constrains shape, not factual correctness; source evidence remains necessary.

Execution must outlive a browser connection or worker process. Persist progress,
pending approvals, and the context required to continue interrupted work. Bind
approval to the exact proposed operation and target version, revalidate before
applying it, and atomically record a stable operation ID and mutation result.
Conversation event replay alone is not execution recovery. Define cancellation
explicitly rather than treating a disconnected browser as a cancellation request.

Compare [Lambda durable functions and Step Functions](https://docs.aws.amazon.com/lambda/latest/dg/durable-step-functions.html)
on the same small workflow before selecting orchestration. Checkpointed execution
still requires [idempotent business effects](https://docs.aws.amazon.com/lambda/latest/dg/durable-execution-idempotency.html).
Keep orchestration adapters outside domain rules and preserve local execution.
Prototype authenticated progress delivery separately: API Gateway supports
[REST API response streaming](https://docs.aws.amazon.com/apigateway/latest/developerguide/response-transfer-mode.html),
while [Python Lambda streaming](https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html)
needs an appropriate runtime integration or Web Adapter. Verify region support,
Python/async integration, and Terraform support before choosing the topology.

Provider-managed [background responses](https://developers.openai.com/api/docs/guides/background)
and [webhooks](https://developers.openai.com/api/docs/guides/webhooks) are options
for slow model calls, not replacements for application workflow state. Adopting
them requires an explicit decision about response retention, completion recovery,
and duplicate notifications under the application-owned context policy.

Use representative live evaluation and source review to choose retrieval and
model settings. Measure useful completion, exact identity, supported fields,
appropriate clarification, latency, and cost per successful task. Measure prompt
cache reuse before depending on it for cost estimates. Vector search, multi-agent
delegation, and fine-tuning remain deferred options requiring a demonstrated
quality or performance need, not prerequisites for serverless AI.

## Operational boundaries

Keep one owner per installation. Authentication protects both UI and API;
mutation approval remains a separate application policy. Require HTTPS, secure
configuration, scoped deployment permissions, monitoring, and backup/restore.

Treat code rollback and data recovery separately: an older image may not understand
a newer schema. A release strategy must explain that boundary. Teardown must
explicitly distinguish removable infrastructure from data/backups retained for
recovery. Preserve the original local data while rehearsing migration on a copy.

## Decisions still needed

- Budget for infrastructure and external OpenAI/retrieval calls.
- Intended access, region, acceptable maintenance downtime, maximum data loss,
  and recovery time.
- Compute and ingress services, cloud database, identity provider, DNS, and
  secret delivery.
- Durable job needs, Terraform state ownership, release/rollback strategy, and
  backup retention.

Use fresh-account deployment, upgrade/rollback, restore, performance measurement,
and teardown exercises to evaluate the choices. Store results with the run or
release; this document records the reasoning, not a readiness badge.
