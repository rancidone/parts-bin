# Parts Bin agent evaluations

Use recorded scenarios to check orchestration reproducibly. Inspect
[scenarios.json](scenarios.json) and [runner.py](runner.py) for the executable
format and assertions. Prefer outcome checks over exact model wording.

Run deterministic fixture evaluations with:

```sh
uv run pytest evaluation
uv run python -m evaluation.runner --workspace /private/tmp/parts-bin-evals
```

For live evaluation, explicitly configure a provider and use the runner's
`--live-factory module:function` option with `PARTS_BIN_LIVE_EVAL=1`.
Account for provider costs. Keep credentials and private user content out of
recorded artifacts. Use `uv run python -m evaluation.runner --help` for CLI options.

See [evaluation decisions](../docs/design/evaluation.md) for the distinction
between deterministic checks, live quality measurements, and source review.

The [enrichment acceptance set](enrichment/acceptance.json) supplies representative
inputs. Its companion
[PBSS5350T expected output](enrichment/pbss5350t.expected.json) records source
locations and paraphrased evidence for a supplied-datasheet extraction exercise.
These are review fixtures, not recorded model outputs or passing test results.
Source URLs are live and may change; check the referenced document and location.

## Checking enrichment candidates

The separate offline checker reads a candidate result from a JSON file:

```sh
uv run python -m evaluation.enrichment_check exact_pnp_transistor /tmp/candidate.json
```

A proposal uses `outcome: "proposal"` and a `fields` object containing
`manufacturer`, `part_number`, `package`, and `description`. Each field has a
nonempty string `value` and `evidence` with `source_url` (HTTPS), `page` (one-based
integer), and `excerpt` (a short verbatim passage). This is an evaluation input
format, not a replacement for the application's enrichment result contract.

For an ambiguous input, use this shape:

```json
{
  "outcome": "needs_clarification",
  "fields": {},
  "clarification": "Is this an individual LED, a strip, or a PC lighting assembly? Do you have a product link?"
}
```

Use case ID `ambiguous_argb_led` for that example. The TO-92 case additionally
requires `user_assertions: {"part_number": "2N2222", "package": "TO-92"}`.
The clarification cases deliberately propose no verified metadata yet; this
does not prohibit discussing possible identities in conversation.

The checker rejects wrong exact identities/packages, missing evidence details,
unsupported fields such as quantity, and changed user assertions. It performs
no network requests or model calls and writes no inventory. It does not establish
whether an excerpt is authentic or supports the claim, whether a description is
correct, or whether a running application preserved database state.

Exit status **1** means failed mechanical checks. Exit status **2** with a
`needs_review` report means those checks passed but source/semantic review is
still required. Argument/file parsing errors also exit 2 with a CLI error rather
than a JSON report. There is deliberately no automatic acceptance exit status.
Even convincing fabricated evidence must never receive a factual pass.

Run the negative and structural checks with:

```sh
uv run pytest evaluation/test_enrichment_check.py
```
