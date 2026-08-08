---
status: baseline
last_updated: 2026-08-08
---
# Agent evaluation baseline

This is the deterministic, recorded-fixture baseline for Phase 05. It is a
contract check, not a claim about live-model quality.

| Runtime | Passed | Failed | Coverage |
|---|---:|---:|---|
| Codex | 11 | 0 | All common scenarios through the MCP projection |
| OpenAI | 11 | 0 | All common scenarios through native function calls |
| Local | 12 | 0 | All common scenarios plus strict JSON-envelope fallback |

Run it with `uv run pytest evaluation` and
`uv run python -m evaluation.runner --workspace /private/tmp/parts-bin-evals`.

## What is measured independently of prose

The evaluator makes its pass/fail decision from normalized tool-call events,
approval events, error codes, and a post-run SQLite snapshot (parts, pending
reviews, and provenance). Answer assertions are optional semantic cues using
alternatives, never an equality comparison. `test_paraphrased_answer_passes_when_tools_and_state_are_correct`
demonstrates this distinction; the negative evaluator tests prove that an
otherwise plausible answer cannot conceal an unsafe tool call, an invalid
argument, a skipped approval, a wrong database outcome, a full-inventory
request, or an exhausted tool budget.

## Phase 00 policy negative-test traceability

| Policy boundary | Negative coverage |
|---|---|
| Inventory is discovered narrowly, never by full-inventory context | `full_inventory` rejects an unfiltered `search_parts`; `large_inventory_search` requires a filtered, bounded query; `test_initial_model_request_never_contains_a_full_inventory_snapshot` checks all runtime request adapters. |
| No generic database/action/patch tool | `test_canonical_contract_exposes_no_generic_or_direct_database_tool` rejects generic/direct database tool names, while `tool_error_recovery` sends an unregistered tool and requires the stable `invalid_input` result before recovery. |
| Typed requests and validation remain authoritative | `invalid_arguments` rejects an unsupported typed search filter; `incomplete_add` and `duplicate_part` reject unsafe adds. |
| Ambiguous and uncertain mutations are clarified | `ambiguous_reference`, `refuse_ambiguous_delete`, and `photo_identification_uncertainty` forbid mutations and assert unchanged state. |
| Server-side approval cannot be bypassed | `approval_bypass` rejects an update when the scenario does not authorize its approval; update, bulk-edit, and review-resolution fixtures require the full request/decision sequence. |
| Enrichment proposals do not silently overwrite committed data | `enrichment_conflict` requires a pending review while preserving the committed manufacturer; `pending_review_resolution` requires approval and durable provenance. |
| Tool loops and recovery are bounded | `tool_loop` rejects an over-budget sequence; `tool_error_recovery` permits only its declared error and then a bounded recovery. |
| Runtime parity is based on events and durable outcomes, not wording | Every common scenario runs against all three runtime adapters; `paraphrased_answer_passes_when_tools_and_state_are_correct` verifies wording independence. |
| Thread runtime is immutable | `agent_runtime/test_runtime.py::test_approval_is_visible_and_must_be_returned_by_same_thread` rejects rebinding a thread from OpenAI to local. |

The product contract's privacy, credentials, telemetry-redaction, and absence
of migration/fallback paths are architectural and operational boundaries. They
are not simulated by a model fixture: Phase 03 runtime tests cover the bounded
request shape and immutable thread contract; Phase 06/07 own removal and
operational-redaction verification. The evaluation runner neither records
prompts nor image data, and live artifacts remain opt-in and factory-owned.

## Runtime-specific limitations

- The local JSON-envelope fallback is necessarily local-only. It is tested as
  an additional case, never as a reduced substitute for the common native-tool
  scenarios.
- Recorded Codex tests exercise the MCP projection in-process; they do not
  validate a configured external Codex app-server transport. The same common
  event, tool, approval, and database contract still applies to a live run.
- Recorded OpenAI and local tests validate adapter behavior, not provider model
  quality, model vision accuracy, credentials, or network availability. Live
  runs are explicitly opt-in and must use redacted approved artifacts.
