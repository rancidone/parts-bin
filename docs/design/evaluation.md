# Evaluation decisions

## Different checks answer different questions

Deterministic tests check domain rules, tool restrictions, approvals, and resulting
data. Recorded model turns exercise orchestration reproducibly, but cannot prove
that a live model identifies parts correctly or follows the same path.

Live evaluation explores model behavior, retrieval quality, latency, and cost.
It requires explicitly configured providers and appropriate handling of private
inputs. Keep synthetic regression scenarios separate from captured user content.
An external-provider failure is different from an invalid domain operation.

Enrichment also needs source review: a well-formed result with a plausible URL
can still contain fabricated evidence. Mechanical checks may reject incorrect
identity or missing evidence, but must not label a result factually correct merely
because its shape matches. Descriptions need semantic review; package and part
identity can often use deterministic comparisons.

## Evidence belongs with execution

Assert outcomes in tests and keep run results with the run or release. Do not
maintain passing counts, coverage matrices, or an implementation baseline in
Markdown. Tests should demonstrate meaningful failures as well as successful
paths; exact model wording is rarely the property that matters.

See [evaluation usage](../../evaluation/README.md) for commands and input examples.
Inspect [the tests](../../evaluation/) for assertions and limitations. A restored
database and an end-to-end provider request establish different things from a
unit-test run; operational exercises should make their scope explicit.
