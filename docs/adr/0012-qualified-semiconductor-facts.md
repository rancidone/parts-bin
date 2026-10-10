# 0012: Preserve semiconductor subtype and measurement conditions

Status: Accepted

## Context

Historical inventory can use the generic label `transistor`. Treating that label
as BJT would misclassify MOSFETs. Semiconductor characteristics depend on test
current, voltage, temperature and pulse conditions. Diode continuous, repetitive
peak and surge limits are different ratings.

Representative manufacturer sources are the Nexperia
[BC847 series](https://assets.nexperia.com/documents/data-sheet/BC847X_SER.pdf),
[2N7002](https://assets.nexperia.com/documents/data-sheet/2N7002.pdf), and
[1N4148/1N4448](https://assets.nexperia.com/documents/data-sheet/1N4148_1N4448.pdf).
Their tables illustrate governing headers, row conditions and measurement endpoints.

## Decision

Use separate BJT and MOSFET contracts and categories for identified devices.
Generic transistor records remain valid unresolved inventory, with no electrical
contract. Clarify their identity and request an explicit category correction before
electrical extraction. Do not infer a subtype or rewrite stock automatically.
Normalize explicit category synonyms for discovery and search, keeping BJT,
MOSFET and generic transistor categories distinct.

Add diode facts with separate continuous, repetitive peak and nonrepetitive surge
bases. Extend minimum source qualifiers to semiconductor characteristics and
current limits. Preserve additional source conditions, including mounting and
duty cycle, even when not universally required. Threshold facts retain whether
the value is minimum, typical or maximum and cannot establish fully-on gate drive.

## Consequences

Existing evidence and pending reviews remain stored. Previously accepted facts
that omit required conditions become incomplete for confirmed matching; they are
not deleted or silently enriched. Review and exact-source identity rules remain
those of [0007](0007-reviewed-electrical-facts.md) and
[0011](0011-best-effort-pdf-extraction.md). No data migration is needed.

Minimum qualifiers are conservative checks, not proof of complete source
interpretation or circuit suitability. Recorded tests verify validation and
matching; live extraction quality requires separate representative evaluation.
