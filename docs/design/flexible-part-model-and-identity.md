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

Use concrete ingestion and search failures to justify additional structure.
Determine which attributes must be searchable and which only need display before
choosing a schema. Preserve quantity, identity, and provenance across migration.
Do not couple this redesign to cloud database selection without a demonstrated
need. The [domain models](../../domain/models.py) and [schema](../../db/schema.sql)
are the place to inspect implemented structure.
