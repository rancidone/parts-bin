# 0006: Normalize in the domain without rewriting or merging historical stock

Status: Accepted

Recorded: 2026-10-10 (existing project decision)

## Context

Historical stock can contain equivalent value spellings. Rewriting records at
startup risks identity collisions and changes to timestamps or evidence; nominal
equality alone does not show that stock is interchangeable.

## Decision

Apply shared domain normalization to writes and compare recognized nominal units
for search without rewriting old rows. Preserve exact ordering suffixes and
unknown package identity. Resolve ambiguous duplicate identities explicitly rather
than combining quantities. Keep original values in accepted provenance.

## Consequences

Lookup can recognize equivalent values while retaining existing IDs and history.
Search equality does not establish ratings or change stock identity. Broader
candidate reads are acceptable locally; indexed canonical fields need measured
costs and an explicit migration. Unrecognized units remain uninterpreted.

[Normalization](../../domain/normalization.py) and [nominal search](../../domain/search.py).
