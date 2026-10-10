# Parts Bin agent evaluations

Use recorded scenarios to check orchestration reproducibly. Inspect
[scenarios.json](scenarios.json), [inventory_lookup.json](inventory_lookup.json),
and [runner.py](runner.py) for the executable format and assertions. Prefer
outcome checks over exact model wording.

Run deterministic fixture evaluations with:

```sh
uv run pytest tests/evaluation
uv run python -m evaluation.runner --workspace /private/tmp/parts-bin-evals
```

For live evaluation, explicitly configure a provider and use the runner's
`--live-factory module:function` option with `PARTS_BIN_LIVE_EVAL=1`.
Account for provider costs. Keep credentials and private user content out of
recorded artifacts. Use `uv run python -m evaluation.runner --help` for CLI options.

Custom runtime factories receive the inventory repository, conversation repository,
and recorded turns; use those injected stores for all state. The runner seeds and
checks through the same repository contracts. Python callers can supply a
`storage_factory` to `run_scenario` or `run_recorded` for isolated adapter testing.
The default factory allocates a fresh SQLite pair per scenario. Custom storage
factories must supply isolated disposable stores; seeding writes fixture inventory.
Consult the callable contracts in [runner.py](runner.py) before updating a live
factory. Do not reconstruct a store from a local filename inside a runtime factory.

For natural-language lookup, evaluate requests such as “Find four 10 kΩ
resistors”, “Do I have 0.1 µF capacitors?”, and exact ordering codes with meaningful
suffixes. Include uncertain packages, insufficient stock, and requirements such
as tolerance or power when candidates lack sourced ratings. The tool supports
nominal-value equivalence and `minimum_quantity` per committed record; it does not
combine separate stock records or inspect pending reviews. Deterministic domain
and tool tests verify matching independently of live model interpretation.

Category lookups first use `list_categories` to discover exact committed names
and summary counts, then retrieve relevant records with `search_parts`.
Discovery includes out-of-stock categories and excludes pending category changes.
The op-amp scenario checks discovery of “operational amplifier” while keeping
“audio amplifier” separate; live runs measure whether the model makes that choice.

The checked-in inventory lookup scenarios use isolated synthetic stock and a
recorded transport, so they make no provider requests. They assert the exact
committed record IDs returned for equivalent value notation, exact ordering
suffixes, package and unit ambiguity, per-record stock thresholds, partial
markings, and a value that exists only in pending enrichment. Electrical lookup
compares accepted synthetic source facts separately from accepted user assertions
and pending source proposals. Every lookup checks that inventory, reviews, and
evidence are preserved. The clarification checks require representative
semantic cues but deliberately do not score exact prose.

To run only that fixture set offline:

```sh
uv run python -m evaluation.runner \
  --scenarios evaluation/inventory_lookup.json \
  --workspace /private/tmp/parts-bin-lookup-evals
```

Run a paid lookup measurement with an explicit model and an environment-provided
`OPENAI_API_KEY` (never place the key in recorded artifacts):

```sh
# If using an ignored local .env with shell exports, first run: source .env
PARTS_BIN_LIVE_EVAL=1 uv run python -m evaluation.live_lookup \
  --model YOUR_MODEL --workspace /private/tmp/parts-bin-live-lookup
```

The command uses the production Responses transport and runtime with fresh
synthetic repositories, ignores recorded turns, and never loads application
configuration or production inventory. It runs all lookup cases by default;
`--scenario SCENARIO_ID` selects cases. Each case makes at most eight model
requests. Provider failures stop the run without retry; rerunning is an explicit
paid action. Each run gets a new directory, with the report updated after each
case and disposable inventory/conversation databases retained for inspection.

The report contains returned model snapshots, per-call usage and latency, visible
events, recorded-contract failures, and whether stock, metadata/electrical reviews,
accepted specifications, or provenance changed.
Missing usage stays unknown. Cost is left unknown until calculated from the
returned models and current account pricing. Review the report's answers and tool
results for correctness and clarification usefulness. Live-specific constraints
allow electrical lookup to inspect facts and retry a qualified query; its final
specification result must still identify the expected confirmed and incomplete
records. Recorded cases retain exact result counts. Wording checks and recorded
tool sequences can reject valid live behavior, and passing them cannot prove that
an answer is correct. Record that review beside the run artifacts, outside the
checked-in fixtures. Exit status 1 means a recorded-contract failure, changed
inventory, request limit, or provider failure; inspect the report to distinguish
these outcomes. Offline runs remain separate from live interpretation quality.
Review incorrect stock totals, omitted pending proposals, and mismatched condition qualifiers even
when the tool sequence is correct. Retain the original report when rerunning failed
cases after an instruction change. This follows the [official OpenAI evaluation
guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
to combine task-specific checks with human judgment.

Deterministic tests and recorded turns check rules and orchestration; live runs
measure model and retrieval behavior. Source review is needed to establish factual
correctness. Keep synthetic scenarios separate from private captured content.

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
uv run pytest tests/evaluation/test_enrichment_check.py
```

## Supplied-source extraction exercise

For electrical extraction, use the bounded runner with the
[primary-source cases](enrichment/electrical_sources.json):

```sh
PARTS_BIN_LIVE_EVAL=1 uv run --env-file .env python -m evaluation.live_electrical \
  --model YOUR_MODEL --workspace /private/tmp/parts-bin-live-electrical
```

The environment file supplies `OPENAI_API_KEY`; keep it untracked. Each run creates
fresh disposable inventory and makes at most one Responses request per case.
Use `--case CASE_ID` to select cases. Retrieval failures make no model request,
and provider HTTP failures stop the run without automatic retries. The runner
does not use the application's inventory or configuration.

The op-amp cases exercise exact B/BA grades, operating supply endpoints versus
stress ratings, typical versus guaranteed parameters, and missing facts. Supply
requirements must check both operating endpoints with the same source conditions.
Typical bandwidth, slew rate and quiescent current do not establish guaranteed
performance. Inspect global table headings as well as individual rows; compare
per-amplifier and whole-device current only with matching scope. A genuine quote
from a neighboring grade cannot establish the requested variant's specification.

The saved-link cases set `linked_part` and seed that URL on disposable stock.
They exercise the operator association separately from supplier-discovered exact
identity: an omitted package/shipping suffix can retain shared ratings, while an
unrelated saved document must still yield no facts. To select these cases:

```sh
PARTS_BIN_LIVE_EVAL=1 uv run --env-file .env python -m evaluation.live_electrical \
  --model YOUR_MODEL --workspace /private/tmp/parts-bin-live-electrical \
  --case opamp_saved_link_missing_suffix --case saved_link_wrong_document
```

Bounded source selection reserves stress, recommended operating and electrical
sections before repeated ordering rows. Detected merged cells on selected pages
retain their bounds as interpretation context. Check that row-specific conditions
override global defaults and that every fact repeats its inherited conditions;
retained source context alone does not establish complete extraction.

Keep the resulting report outside the repository. It records source hashes,
model usage, latency, candidate passages, failure classifications and approval,
restart, replay and quantity checks. Approval in these disposable databases tests
mechanics only. Independently inspect the cited source pages for exact identity,
values, basis, complete conditions and omitted facts, and record that review
alongside the report. In particular, check rating temperature, min/max endpoints,
continuous/pulsed current and current/voltage pairs. Genuine passages can still
be misinterpreted. A completed run exits 2 because semantic review is required;
it never labels a candidate factually correct from structural checks alone.

Use the operator command on an isolated copy of the inventory/configuration first.
Choose an existing part ID whose exact part number is `PBSS5350T`; the command does
not create parts. Set `PARTS_BIN_ENRICHMENT_MODEL` to the OpenAI model you want to
evaluate, then run:

```sh
uv run python -m ingestion.enrich_source PART_ID \
  https://assets.nexperia.com/documents/data-sheet/PBSS5350T.pdf \
  --model "$PARTS_BIN_ENRICHMENT_MODEL" --config /path/to/isolated-config.toml
```

Replace `PART_ID` and the config path. An uncached extraction makes one billable
Responses API request using the key in `[agent.openai]`. Model charges depend on
the selected model and usage; the output includes token usage. There is no web
search in this exercise. Inspect `--help` and
[the implementation](../ingestion/supplied_source.py) for source hosts and limits.

The command stages a pending review for supported fields. Inspect evidence and
accept/reject through the application. It refuses to replace an existing review
or stage against changed metadata. Cached results remain separate from each
part's review. `--refresh` retrieves the source again; changed content can incur
another model call. A failed attempt has a brief cooldown rather than automatic
retries. This command is not a durable cloud queue or a shared spending cap.

The JSON output can be saved outside the repository and passed to the offline
checker above. Quote-presence checks reject fabricated passages, but genuine
quotes can still be misinterpreted. Compare the description and package against
the document and fixture; record correctness, latency, and usage with the exercise.

Structured extraction uses a strict schema because its fields are explicit and
unsupported values are null. This differs from optional inventory patch tools.
See the official [structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs).

## Independent PDF completeness and OCR experiments

`enrichment/electrical_expectations.json` contains expectations independently
reviewed from rendered source pages. Keep this file out of model inputs. Recheck
the pages before changing expectations; changed source hashes invalidate scores.
The comparator flags missing values, qualifiers, mismatched value/condition
pairs and unsupported application ratings. Unlisted equivalent wording and
quotation interpretation still require visual review.
Saved-link cases can name an `expectation_case_id` when the independently reviewed
ratings apply to the same electrical grade. The report retains both case IDs and
still checks the captured source hash. This association is evaluation metadata;
it is never supplied to the extraction model. Reviewed equivalent qualifier
wording belongs in the expectations; missing conditions remain failures.

```sh
uv run python -m evaluation.electrical_completeness /path/to/report.json \
  --output /path/to/completeness-review.json
```

The command exits 2 to require review. Its scores compare source expectations,
whereas the application assessment measures supported-field coverage with
minimum qualifiers. These denominators differ, and neither score certifies
correctness. Keep run reports, source captures and completed reviews outside the
repository. Distinguish fresh model runs from offline replay of recorded responses.
The B/BA op-amp expectations distinguish the temperature-dependent offset bounds,
negative bias-current magnitudes, and per-amplifier typical current. Inspect
merged condition cells and row overrides even when global headings are retained.
Source hashes may differ with request headers; review the exact capture used by
the application before updating an expectation hash. A visually matching capture
does not justify weakening the hash check.

`evaluation.pdf_ocr.raster_ocr` renders explicitly selected pages at 150 or 300 DPI
and invokes an installed Tesseract executable. It is evaluation-only: at most
three pages, bounded pixels/text/output and a per-page timeout. Preserve the
original source hash and page numbering when passing transcripts into extraction;
record raster hashes and recognition measurements beside the candidate. Compare
with an image-only baseline and independently reviewed source expectations.
Rasterizing clean digital pages tests controlled text recovery, not real scan
quality or automatic page discovery. Recognition confidence does not measure
cell association, unit meaning or electrical correctness. The production path
continues to direct image-only sources to visual review.
