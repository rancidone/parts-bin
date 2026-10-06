# Phase 07 — Operations and continuous improvement

Depends on: Phases 00–06.

## Implementation prompt

```text
Add production-quality local operational support for the hybrid agent architecture. Emit structured, privacy-preserving telemetry for runtime selection, turn latency, tool-call lifecycle, tool errors, approval decisions, loop-limit failures, and final domain outcome. Do not log prompts, image payloads, credentials, or complete inventory records by default.

Document runtime setup, local-model capability requirements, Codex authentication, OpenAI API configuration, backup/recovery, and diagnostic commands. Add a redacted evaluation-failure capture flow that can promote approved failures into the Phase 05 scenario suite. Do not reintroduce an old runtime or a compatibility mode as an operational escape hatch.
```

## Testing prompt

```text
Test telemetry schemas and redaction with representative messages, images, tool arguments, failures, and approvals. Verify metrics are consistent across every runtime, do not contain secrets or raw user content by default, and survive partial runtime failure. Test the documented diagnostics against a clean local setup.
```

## Verification prompt

```text
Verify operational readiness by running a local-model session, an OpenAI API session when configured, and a Codex session when configured. Confirm logs distinguish runtime/tool/domain failures, telemetry is redacted, backups restore the inventory database, and a newly observed failure can be converted into a regression scenario. Report unsupported deployment modes explicitly.
```

## Chat ingestion and correction regressions

Named IC additions include functional identification in the assistant's instructions
and automatically run supplier lookup through the domain service. Lookup results
are staged for review, with failures reported in the addition's `enrichment`
result; supplier failures do not undo stock or invite duplicate retries. Ambiguous
base numbers must not imply a package or manufacturer, and uncertain package
variants require clarification before merging stock.

The Codex exec adapter projects completed MCP activity into conversation events
and routes `approval_required` results to the gateway's approval controls. An
accepted approval executes the saved tool arguments directly before model
continuation. The continuation receives the committed outcome. Recorded approval
scenarios therefore contain no second model-generated mutation call.

Chat message and approval endpoints stream persisted events while the turn is
running. Codex tool starts and results are forwarded as JSON lines arrive, and
are not replayed at turn completion. SSE responses disable proxy buffering and
caching. Client disconnection cancels the turn producer and its Codex subprocess;
already persisted events remain available through thread replay. Assistant text
is currently emitted as a complete message, while tool activity streams live.
