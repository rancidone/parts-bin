# 0022: Compare whole specification value intervals

Status: Accepted

## Context

Inventory lookup scenarios ask whether a regulator's operating input endpoints
cover a requested supply. Reviewed MOSFET source cases also describe gate
threshold intervals. Scalar-only values cannot preserve both bounds or directly
check a supply interval. Choosing one endpoint can overstate suitability.

## Decision

Move scalar and interval parsing into a shared quantity module, extending
[0021](0021-unit-aware-condition-intervals.md). Accept nonnegative ordered numeric
value intervals and center plus tolerance through the existing fact review and
requirement contracts. Preserve original values and evidence.

Equality confirms identical endpoints. At least confirms when the stored lower
endpoint is at least the requested upper endpoint; at most confirms when the
stored upper endpoint is at most the requested lower endpoint. A stored interval
that cannot satisfy the requested bound excludes the candidate. Uncertain overlap
remains incomplete. Scalars are degenerate intervals, preserving scalar behavior.

For operating coverage under identical conditions, compare the operating minimum
at most the requested interval and operating maximum at least that interval.
Keep minimum and maximum bases distinct from stress ratings. Condition containment,
interpolation, thermal inference and derating remain unsupported. Nominal passive
identity facts stay scalar, with tolerance recorded separately.

## Consequences

HTTP, agent tools and UI searches use the same domain comparison. Recorded and
mocked source checks can exercise ranges without model calls. Existing storage
requires no migration. Older releases cannot interpret new interval facts, so
use a release that supports approved interval facts. Restoring a compatible
backup for an older release requires deliberately reconciling subsequent inventory
and conversation changes through the documented recovery procedure.
