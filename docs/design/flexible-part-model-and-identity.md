# Part identity beyond manufacturer numbers

## Design question

How should inventory represent connectors, switches, modules, kits, and unmarked
parts without inventing a manufacturer number or forcing irrelevant fields?
This remains a design question, not an approved schema migration.

## Options and tradeoffs

A small common record plus typed category-specific attributes can express these
parts more honestly than a universal list of required fields. It also makes
validation, filtering, editing, and migration more complex. Free-form attributes
are easy to store but hard to compare safely; a large taxonomy can create more
maintenance than the inventory warrants.

Identity needs deterministic rules appropriate to the available evidence.
Manufacturer and exact ordering code may identify a semiconductor, while value
and package may identify a passive for a user's purposes. Modules and connectors
may need several physical attributes or explicit user confirmation. Missing
identity information must not accidentally merge distinct stock.

Preserving user-entered representations alongside normalized comparison values
can improve display and search consistency. Historical normalized data cannot
reconstruct the user's original wording; do not invent it during migration.

## Decision criteria

The same modeling question applies to enrichment. Resistors need resistance,
tolerance, power, and temperature coefficient; transistors need device type,
qualified voltage/current limits, and gain; audio ICs need supply range, channels,
and performance under stated test conditions. Prefer a common inventory record
with typed category-specific specifications over making every part share all
possible fields. Each proposed fact still needs evidence and domain validation.
Unknown values must remain unknown, and proposed specifications must pass review.

Flexible shape does not require choosing NoSQL. [DynamoDB items can have distinct
attributes](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.CoreComponents.html),
while [PostgreSQL JSONB supports flexible documents and indexing](https://www.postgresql.org/docs/current/datatype-json.html).
DynamoDB queries are organized around keys and secondary indexes. Establish the
needed cross-category and numeric searches before comparing storage options.
Keep accepted specifications inside the authoritative repository boundary;
adding a separate enrichment database would add synchronization and recovery
work. This is a modeling direction to evaluate, not a selected cloud backend or
an approved schema migration.

Use concrete ingestion and search failures to justify additional structure.
Specification search is a core requirement: an agent must translate a user's
natural-language requirements into bounded queries over committed inventory.
Determine which attributes must be searchable and which only need display before
choosing a schema. Preserve quantity, identity, and provenance across migration.
Do not couple this redesign to cloud database selection without a demonstrated
need. The [domain models](../../domain/models.py) and [schema](../../db/schema.sql)
are the place to inspect implemented structure.

## Natural-language requirements search

The model interprets the request and asks targeted questions about ambiguous
requirements. A narrow typed tool accepts explicit constraints; the domain
validates their meaning and the repository executes the query. The agent must
discover supported specification names, units, qualifiers, and comparisons
through a bounded category specification contract rather than inventing fields
or receiving the full inventory. Neither raw SQL nor arbitrary document paths
belong in the model-facing tool contract.

Support combined categorical, numeric, range, and stock constraints. Normalize
units before comparison while preserving the original evidence. A stored
operating range must cover a requested operating point or interval; an absolute
maximum is not evidence of a valid operating range. Continuous and pulsed ratings,
typical and guaranteed values, and condition-dependent measurements must remain
distinct. A request such as "handles 1 A" may need voltage, duty cycle, temperature,
and application clarification before a useful search can be expressed.

Representative queries to drive the specification model and acceptance tests:

| User requirement | Query meaning |
| --- | --- |
| Four through-hole 10 kΩ resistors, 1% or better, rated at least 0.25 W | Nominal resistance equals 10,000 Ω; tolerance at most 1%; rated power at least 0.25 W under applicable conditions; through-hole mounting; stock at least four |
| NPN transistors with a continuous collector-current rating of at least 1 A | NPN device type and the continuous rating with its stated conditions; do not substitute a pulsed rating or claim application suitability |
| A dual op-amp in SOIC that operates from a single 5 V supply | Two amplifier channels, the specified package, and evidence of single-supply operation covering 5 V; an absolute maximum alone cannot satisfy it |

Hard constraints filter candidates before optional preferences rank them.
Unknown or unsupported facts cannot satisfy a hard constraint. Return confirmed
matches separately from candidates that lack required evidence, with the missing
facts identified. Distinguish no matching committed part from insufficient
enrichment, contradictory facts, and unsupported query fields. Historical user
assertions and independently sourced facts must retain their evidence status;
search must not silently upgrade an assertion into a verified specification.

Return bounded, paginated results with exact part identity, available quantity,
the facts supporting each match, their qualifiers, and provenance references.
The assistant can explain those matches or propose enrichment of uncertain
candidates. Pending enrichment must not make a part a confirmed match before
review. Semantic retrieval may help discover candidates, but similarity alone
cannot establish numeric or electrical suitability.

Storage evaluation must exercise combined filters, boundary values, unit
equivalence, unknown fields, qualifiers, pagination, and updates after accepted
enrichment. Include tests of natural-language interpretation separately from
deterministic query execution. A flexible JSON document alone is not a search
plan: choose indexes or normalized searchable facts for the required comparisons.
[DynamoDB Query requires partition-key equality and limits key conditions to key
attributes](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Query.KeyConditionExpressions.html),
so evaluate the cost of evolving access patterns and filtered reads against
relational queries over typed facts and JSON. Keep this evaluation compatible
with local storage through the repository boundary.
