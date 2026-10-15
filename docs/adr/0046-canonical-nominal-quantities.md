# Canonical decimal nominal quantities

## Context

Inventory stored mixed nominal labels: decimal suffixes, multiplier-as-decimal
notation, and lowercased ohm symbols. Equivalent values could evade duplicate
checks. The operator requested decimal storage for resistance and the same
behavior for capacitance, inductance, current, voltage and other nominal units.

## Decision

The domain owns one nominal-quantity parser and formatter. Store recognized
scalars as exact decimal base-unit strings in `value` and a separate base unit
in `value_unit`. Decimal strings avoid binary floating-point loss while fitting
the inventory's existing text-valued field. Persist units explicitly, including
for categories that do not imply one. Known passive categories establish their
nominal dimension; other categories require explicit units or `value_unit`.

Input accepts existing passive labels and explicit SI units. SI prefixes retain
case; legacy resistor `m` denotes mega, while explicit `mΩ` denotes milli.
The domain derives `display_value`, such as `2R2`, `5k1`, `100nF`, or `500mA`.
Adapters display that label and submit user input through domain validation.
Export includes both the decimal and its unit. Nominal-unit equivalence defines
normalized passive identity; package, exact ordering codes and record IDs retain
their existing meaning. Ambiguous labels, ranges and qualifiers remain text.

Use an explicit, transactional operator migration with a protected backup and a
collision preview. Never normalize historical inventory at startup or merge
quantities automatically. Preserve IDs, quantities, ordering codes, original
source passages, proposals and accepted facts. Rebind a current specification
review's metadata snapshot after a representation-only conversion; leave stale
snapshots stale. Existing approval snapshots become stale when targets change.

This supersedes ADR 0006 for the representation of nominal values and the
operator-authorized historical conversion. Its prohibition on silent stock
merges and evidence rewriting remains in force. Source-backed electrical facts
retain their original text, bases and conditions; the existing exact quantity
parser continues to compare them without promoting a nominal value to a rating.

## Consequences

Writes, imports through the domain, duplicate checks and searches share one
representation. Historical raw imports remain faithful until explicitly migrated.
Run the additive schema upgrade before deploying code that reads `value_unit`.
A code rollback can retain the added column, but older code cannot safely write
nominal values to a converted installation; restore a coordinated backup or
use a compatible application revision. Any identity collision requires explicit
operator resolution before migration can commit.
