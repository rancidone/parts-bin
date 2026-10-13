# 0023: Cover requested conditions within documented operating intervals

Status: Accepted

## Context

A requested operating point can lie inside documented recommended conditions.
The reviewed LM358B source case has supply operating bounds over an ambient
range. TI's [recommended operating conditions](https://www.ti.com/lit/ds/symlink/lm358.pdf),
section 5.3, distinguish these bounds from stress ratings and electrical test
points. Equal-condition comparison alone leaves contained requests incomplete.

## Decision

For accepted source facts with operating_minimum or operating_maximum basis,
allow the requested condition interval to be contained within the documented
interval. Reuse the shared unit-aware parser and one interval-containment helper.
Scalars are degenerate intervals. Require the complete normalized condition-key
mapping on both sides, with exact equality for unknown conditions, relationships
and other nonnumeric qualifiers. Missing conditions or requests extending outside
the documented range remain incomplete.

Retain equality for all other bases, including typical, rated, threshold,
maximum-magnitude and absolute-maximum facts. Numeric containment alone does not
establish their operating applicability. Operating-range searches still need
both endpoint requirements; stress limits cannot replace an operating endpoint.
No interpolation, extrapolation, derating or ambient/junction conversion occurs.
This extends the condition policies in [0021](0021-unit-aware-condition-intervals.md)
and [0022](0022-specification-value-intervals.md).

## Consequences

The domain evaluator provides the same coverage to UI, HTTP and agent searches,
without category-specific comparison code or a new mutation path. Source claims
still require exact identity and review; original values, qualifiers and evidence
remain stored. No data migration is required. More permissive applicability for
other fact bases needs a concrete source-backed requirement and separate decision.
