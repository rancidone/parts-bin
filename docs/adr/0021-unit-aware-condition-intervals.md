# 0021: Compare explicit condition intervals with shared unit-aware parsing

Status: Accepted

## Context

Reviewed op-amp source cases express supply conditions as a voltage interval.
Equivalent interval notation and SI prefixes previously compared literally in
inventory searches. Ranges are also meaningful for current, frequency, time,
resistance, temperature and percentage conditions; a supply-only parser would
repeat the same logic across categories.

## Decision

Use one interval parser for scalars, explicit lower/upper endpoints and center
plus nonnegative tolerance. Convert each explicit endpoint independently to the
named condition's dimension. A unit stated only after the second quantity applies
to both quantities. Compare by exactly equal decimal endpoints, preserving
original conditions, qualifiers and evidence. The offline source-expectation
comparator reuses the parser without replacing independent source expectations.

Extend the condition comparison from [0011](0011-best-effort-pdf-extraction.md).
Unknown condition names, relations and additional annotations remain literal.
Reject reversed intervals, negative tolerances and incompatible units from
numeric comparison. No containment, interpolation or derating is inferred.

## Consequences

Accepted facts with equivalent test conditions can confirm the same requirement
across categories. A scalar inside a nondegenerate interval remains distinct,
and equal endpoints alone do not establish operation or suitability across a
requested range. Stored facts and identities need no migration. Add new named
dimensions through the typed condition map, with representative matching and
mismatch tests, rather than another category-specific parser.
