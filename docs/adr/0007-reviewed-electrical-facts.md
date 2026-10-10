# 0007: Match electrical requirements against reviewed, qualified facts

Status: Superseded in condition comparison by [0011](0011-best-effort-pdf-extraction.md)

Recorded: 2026-10-10 (existing project decision)

## Context

Nominal values and unqualified ratings cannot establish electrical suitability.
Different variants, operating conditions, and rating bases must not be treated
as interchangeable.

## Decision

Store facts and independent specification reviews against exact inventory
records. Preserve original units, bases, conditions, and evidence. Accept changes
through snapshot-bound approval; assertions remain assertions. Discover supported
fields through the category contract and match only committed facts. Return
confirmed matches separately from candidates missing evidence.

## Consequences

Supported units compare with decimal arithmetic; rating bases stay distinct.
Conditions compare complete mappings exactly rather than infer derating or omitted
conditions. Evidenced identity changes require a separate record, and stock
increments require an explicit same-variant target. Definitions are extensible,
not an exhaustive taxonomy. Richer comparisons and automatic extraction need
representative evidence and evaluation.

[Fact contract](../../domain/specifications.py), [service](../../domain/service.py),
and [review procedure](../operations.md#electrical-fact-review).
