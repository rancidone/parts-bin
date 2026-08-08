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
