# 0020: Keep unnumbered non-passive stock as distinct physical groups

Status: Accepted

## Context

The nominal passive identity tuple was also applied to connectors, modules,
kits and unmarked devices. Two groups with the same category, value and package
could be combined despite different descriptions or uncertain identity. These
fields cannot establish that such stock is interchangeable.

## Decision

Retain exact ordering-code identity and existing nominal identity for resistors,
capacitors and inductors. Unnumbered stock outside those categories has no
inferred shared identity. Each addition creates a distinct record, including
repeated rows within a batch. Increasing existing stock requires an explicit
part ID through the shared approval policy. Operation replay retains its saved
result, so duplicate delivery does not create another group.

Narrow the SQLite uniqueness index to nominal passive categories without
rewriting existing inventory or historical records. Typed category attributes
can follow concrete ingestion requirements; descriptions do not substitute for
verified ordering codes.

## Consequences

Groups can share labels and packages without being merged or blocked by an
unrelated record's identity. Operators must select the intended group explicitly
when increasing stock. Previously merged groups cannot be reconstructed
without operator evidence. Rehearse migration on a copy and preserve a backup;
older applications cannot safely manage newly distinct unnumbered groups.
