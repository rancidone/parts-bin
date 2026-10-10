# Reviewed electrical requirements

Electrical facts belong to an exact inventory record, alongside accepted evidence,
inside the authoritative repository. Historical records have no inferred ratings.
Adding these records is an additive SQLite schema change; startup does not rewrite
inventory, metadata provenance, conversations, or approvals. A cache loss cannot
delete accepted facts or pending specification reviews.

The definitions in [the domain contract](../../domain/specifications.py) describe
an initial set of fields for resistors, capacitors, BJTs, MOSFETs, inductors,
transformers, and switches. This is not an exhaustive component taxonomy. Existing
categories remain valid; unknown categories report no supported specification
fields. New definitions use the same fact storage without a schema migration.
Names such as `transistor` do not silently become `bjt`: ambiguous device identity
needs clarification. Further fields need representative evidence and requirements.

Each fact retains its original value with explicit units, basis, stated conditions,
and bounded evidence. Decimal comparisons normalize supported SI units without
rounding or losing prefix case. Categorical facts compare exactly. Different bases
remain distinct: continuous versus pulsed absolute maxima, rated versus operating
limits, saturation current, and threshold voltage cannot substitute for one another.
A component rating is not a claim of suitability for an application.

Source facts require an HTTPS locator, document hash, retrieval time, page, passage,
and the record's exact ordering code. Structural validation cannot authenticate a
passage or show that it supports a rating and its conditions. An operator must
inspect the source and variant before staging, and inspect the proposal before
approval. Unidentified stock can retain assertions but cannot inherit a supplier
variant's sourced ratings. User assertions remain labeled as assertions after
approval; they cannot satisfy a source-backed hard requirement.

Electrical proposals have an independent review so metadata and specification
work cannot overwrite one another. Acceptance merges only proposed fields, retains
other accepted facts, and preserves quantity. A rejected review leaves accepted
facts intact. Approval snapshots include both kinds of review and accepted facts;
changed targets require a fresh approval. Application and its saved outcome share
a transaction, so duplicate delivery replays the original result.

Evidence remains bound to the original identity. Changing category, value, package,
ordering code, or manufacturer while electrical facts or a review exist is refused;
create a distinct record for a different variant. Automatic duplicate stock
increments into these records are refused. Explicitly identify the same variant
and use `add_stock`. This conservative rule avoids promoting uncertain additional
stock to the existing ratings without changing the historical identity key.

The agent discovers fields through `get_specification_contract`. It submits
supported constraints to `search_parts`, combined with exact inventory filters and
a minimum quantity per record. Confirmed matches carry supporting facts and their
evidence. Missing facts, assertions, or mismatched conditions produce incomplete
candidates, not confirmed matches. Known failing constraints exclude a candidate.
Both result groups are bounded, and report total counts and truncation. Pending
proposals never participate in matching.

Conditions currently compare complete mappings exactly. Omitting a stated mounting,
temperature, supply type, or test condition cannot establish a match. This is
conservative; it does not interpolate derating curves or infer equivalent wording.
More structured condition and operating-range comparisons need concrete use cases
and evidence. Queries narrow candidates through the inventory repository, then
compare facts in the domain. Indexed specification access and pagination need
measured inventory scale before an additional storage mechanism is justified.

The model-facing staging tool accepts user assertions only. Retrieved source facts
must enter through inspected source ingestion; the model cannot manufacture a
URL or passage and call it independently sourced evidence. The local operator
import provides that review boundary. Existing supplier lookup still stages base
metadata; automated extraction of these electrical facts is separate work. No
additional paid call or discovery service is introduced by this storage/query slice.
