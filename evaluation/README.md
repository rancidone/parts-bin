# Parts Bin agent evaluations

`scenarios.json` is the version-controlled behavioral contract.  Every case
contains its seeded database, user turns, approval allowance, recorded model
turns, tool restrictions, final inventory/review/provenance assertions, and
semantic answer checks.  Answer checks use cues rather than exact wording.

Run deterministic fixture evaluations with:

```sh
uv run pytest evaluation
uv run python -m evaluation.runner --workspace /private/tmp/parts-bin-evals
```

The normal suite never sends model requests.  A live run is deliberately
opt-in: set `PARTS_BIN_LIVE_EVAL=1` and pass a `module:function` runtime
factory that constructs the chosen `AgentRuntime` with its configured live
transport.  That factory owns any artifact recording and must redact prompts,
images, credentials, and complete inventory records before persistence.

The JSON-envelope case is local-only because it verifies the local runtime's
non-native-tool mode.  All other cases run unchanged against Codex, OpenAI,
and native local runtime fixtures.

See [the deterministic baseline](../docs/design/evaluation-baseline.md) for
the policy-negative-test matrix and runtime-specific limitations.
