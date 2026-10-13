# 0024: Return explained partial inventory matches

Status: Accepted

## Context

An owner can relax a nominal value or electrical requirement when choosing stock.
Discarding every candidate with a known specification failure hides useful
alternatives. Suggesting alternatives without recording their differences can
misrepresent suitability or reuse the wrong physical stock.

## Decision

Keep exact matches separate from explicitly labeled partial_matches. Evaluate
all electrical requirements, retaining each known source-backed failure with
its original requested comparison, value, basis, conditions and actual fact.
Retain supported facts and unresolved requirements too. Unknown evidence or
mismatched conditions do not become confirmed failures or successes.

For an explicit passive category and nominal value filter, compare committed
resistor, capacitor and inductor values through the existing dimension-aware
nominal parser. Return numeric alternatives with requested and actual values.
Do not relax category, profile, package, full ordering code or minimum stock.
BOM checks and mutation identity rules continue using exact search semantics.

Rank partials by confirmed requirement count, then unresolved requirement count,
known mismatch count, normalized numeric shortfall and stable ID. Normalize a
known failing interval's gap by the larger endpoint magnitude so mixed dimensions
can be compared without raw voltages dominating currents. Zero values do not
cause division by zero. Categorical failures receive equal distance rather than
invented semantic similarity. This ranking is review assistance, not confidence,
interchangeability, or an assumed acceptable tolerance.

Paginate confirmed, incomplete and partial groups independently with the shared
limit and offset. Publish counts and next_offset for every group. UI and agent
contract explanations must call partials out and identify every mismatch.
The owner decides which requirements to relax before selecting an alternative.

## Consequences

A typed domain assessment supports HTTP, UI and agent searches without repeating
comparison rules. Shared quantity parsing and gap calculation serve both nominal
and electrical alternatives. Source evidence, stock identities and quantities
remain unchanged; no migration or provider calls are needed. This changes the
candidate-exclusion policy from [0022](0022-specification-value-intervals.md).
