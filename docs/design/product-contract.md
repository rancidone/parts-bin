---
status: target-contract
last_updated: 2026-08-08
---
# Parts Bin product contract

## Purpose and scope

Parts Bin is a single-user, local-first electronics inventory application. A
user can browse inventory and use text or photos to identify, add, find, and
maintain parts. Inventory facts and mutations are authoritative only when
obtained through Parts Bin's typed domain and tool contract.

This contract defines the target system for the migration in `TODO.md`. It
supersedes earlier design documents where they describe the legacy LLM chat
path. It does not itself change application behavior.

## Target architecture and ownership

| Layer | Owns | Must not own |
|---|---|---|
| Domain service | Typed inventory, enrichment-review, provenance, normalization, duplicate detection, validation, transactions, and stable domain errors | HTTP, SSE/event formatting, prompts, model clients, or UI state |
| Tool registry | The single typed Parts Bin tool definitions, JSON-compatible schemas, input/output validation, and mapping to domain operations | Persistence access or runtime-specific business rules |
| MCP server | The MCP projection of that registry for Codex | A different tool set or application logic |
| Agent runtime | Model transport, bounded tool loop, normalized event emission, and final assistant response | Inventory rules, persistence, approval decisions, or full-inventory prompt context |
| Agent gateway | Thread creation, immutable runtime selection, runtime lifecycle, durable visible events, and approval round trips | Inventory queries, SQLite access, or domain rules |
| React UI | Rendering the normalized event stream and invoking approved application APIs | Runtime fallback policy or inventory business rules |

The system supports exactly three selectable runtimes: `codex` (Codex app
server through Parts Bin MCP), `openai` (OpenAI Responses API with native
function calling), and `local` (an OpenAI-compatible local inference server).
All use the same registry, domain service, approval engine, and visible event
model. A local runtime without native tool calls may use one strict,
documented JSON tool-call envelope; its arguments and results undergo the same
validation as native calls. Tool loops are bounded and report an explicit
loop-limit error.

## Tool and data contract

The canonical tool set is:

`search_parts`, `get_part`, `add_part`, `add_stock`, `update_part`,
`bulk_update_parts`, `delete_part`, `lookup_part_specs`,
`list_pending_reviews`, `apply_review`, `reject_review`, and `get_provenance`.

Tools have small, strict, JSON-schema-compatible inputs and compact results by
default. They return stable domain errors for invalid, missing, ambiguous, or
conflicting requests. Queries read committed inventory only; pending enrichment
reviews never become inventory facts until accepted. Supplier lookup stages
proposals and provenance rather than silently replacing committed data.

Models must discover inventory through tools. They must never receive a full
inventory dump, raw database access, credentials, unrestricted patches, or a
generic action envelope. No raw SQL, generic `run_action`, arbitrary patch, or
model-controlled approval-bypass tool is permitted.

## Approval policy

The server, not a model, enforces approval. Read-only tools execute without
approval. A mutation that changes or deletes identified committed inventory,
applies a review, or performs a bulk update requires an explicit approval event
and a corresponding user response before execution. Approval requests name the
target and intended effect; ambiguous targets are clarified, not guessed.

`add_part`, `add_stock`, and `lookup_part_specs` may execute without a separate
approval only when the typed request is complete, unambiguous, and satisfies
domain duplicate and validation rules. Any duplicate, incomplete, or
uncertain identification requires clarification before a mutation. The
approval engine records the decision as a visible conversation event and
cannot be bypassed by runtime, UI, or MCP transport.

## Threads, events, and runtime selection

Runtime choice occurs only while creating a new conversation thread. The choice
is stored with that thread and is immutable for its lifetime; reconnecting or
resuming uses the original runtime. The UI offers no per-turn backend toggle or
silent fallback. Startup or transport failure is reported as a runtime error;
the user may begin a separate thread using another runtime.

Every runtime emits one normalized stream containing assistant text, tool
activity, structured inventory results, approvals, errors, and completion.
The gateway persists the visible conversation events needed for a thread to
resume. Event and database outcomes, not exact model wording, define runtime
parity.

## Privacy and operational boundaries

The local inventory database, user text, photos, credentials, and complete
inventory records stay local by default. A selected cloud runtime necessarily
sends only the user input, bounded conversation context, and tool definitions
or tool results required for that turn to its configured provider; it never
sends the full inventory or credentials. A selected local or Codex runtime is
subject to its configured local/app-server transport boundary.

Operational telemetry is structured and redacted by default. It may include
runtime selection, latency, tool lifecycle, errors, approvals, loop-limit
failures, and final domain outcome. It must not include prompts, image payloads,
credentials, or complete inventory records by default.

## Explicit non-goals

- Multi-user access control, tenancy, or remote inventory synchronization.
- Generic database, shell, web-browsing, or arbitrary-action agent tools.
- Full-inventory prompt injection, semantic inventory retrieval, or a model
  deciding database writes without server-side validation.
- A fourth runtime, automatic runtime fallback, migration shims, compatibility
  layers, feature flags, aliases, or parallel old/new chat paths.
- Exact prose matching as the criterion for agent correctness.

## Migration exit criteria

| Phase | Measurable exit criterion |
|---|---|
| 00 — Product contract | This document defines ownership, all three runtimes, tools, approvals, privacy, immutable threads, deletion scope, non-goals, and criteria for phases 01–07. |
| 01 — Domain service | All inventory and enrichment mutations use typed domain operations; direct domain tests cover rules and rollbacks; adapters contain no duplicated business rules. |
| 02 — Tool registry and MCP | Registry schemas are canonical and MCP exposes an identical tool contract; real MCP transport tests cover every tool, validation, errors, and approval enforcement. |
| 03 — Agent runtimes | Codex, OpenAI, and local runtimes pass the same deterministic behavior suite with registry tools, normalized events, shared approvals, bounded loops, and immutable thread selection. |
| 04 — App host and UI | One gateway and one UI event-stream client support all event types and approval round trips; the runtime selector is new-thread-only and legacy chat/query UI calls are absent. |
| 05 — Evaluation suite | Versioned scenarios assert tool constraints, approvals, and final database/review/provenance state for every runtime using deterministic fixtures. |
| 06 — Atomic cutover | The only conversational path is gateway + registry + normalized events; `llm/client.py`, `ConversationHistory`, `db_action`, full-inventory prompting, legacy `/chat` and `/query` paths, backend-toggle UI, old fallback transport, and their tests/docs/configuration are deleted. Existing SQLite data opens successfully. |
| 07 — Operations | Redacted cross-runtime telemetry, setup/backup/diagnostic documentation, and an approved failure-to-regression flow are verified on supported configured runtimes. |

## Phase 00 acceptance checks

- A reviewer can map each current legacy behavior to exactly one later phase:
  embedded server business rules (01); no shared registry/MCP (02); `LLMClient`,
  direct chat-completions, `ConversationHistory`, and fallback transport (03);
  `/chat`, `/query`, legacy SSE, and backend toggle (04); missing cross-runtime
  evaluation (05); and unredacted/legacy operational assumptions (07).
- Phase 06 separately verifies repository-wide removal of the obsolete paths,
  tests, documentation, and configuration identified by the Phase 06 exit
  criterion; this cleanup does not reassign the behaviors to a second phase.
- No Phase 00 code changes introduce a compatibility path or runtime behavior.
- Every later phase can be tested against a boundary and outcome stated above.
