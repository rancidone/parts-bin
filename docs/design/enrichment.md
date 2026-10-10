# Evidence-backed enrichment

## Decision and rationale

Use on-demand manufacturer/distributor evidence with model-assisted extraction.
Stop maintaining a local bulk JLCPCB/LCSC catalog as a product dependency. The
catalog's download, extraction, storage, and operational UI add complexity
without establishing that a proposed specification matches the user's exact part.
This is the chosen direction, not a statement about removal from the code.

A model is useful for interpreting varied document layouts and language. Its
memory is not an authoritative source of specifications. Search can locate
candidate sources; it does not verify their contents or the identity of the
physical part. Retain individual supplier integrations only when measurements
justify their reliability and maintenance cost.

The earlier design prescribed an ordered catalog/API/page/PDF/search fallback
chain and multiple specialized parsers. Prefer a bounded retrieval and extraction
path with clear failures. More fallback stages do not inherently create trust.

## Identity before fields

Match the exact manufacturer and ordering variant before assigning variant-specific
facts. Preserve meaningful suffixes. A family datasheet may describe several
packages or complementary devices; a plausible match is not enough.

Keep unsupported fields unknown. Ask targeted questions when a marking, product
link, or photo could resolve ambiguity. Distinguish the user's assertion from an
independently verified fact. Descriptive text must preserve numeric qualifiers:
absolute maximum versus operating limits, pulse conditions, and polarity.

## Evidence and review

Each proposed factual field needs an attributable source and a location or passage
that supports that value. A URL alone is insufficient evidence. Contradictory
sources should produce a visible conflict, not silent selection of whichever
value arrived first. See [retrieval decisions](source-retrieval-and-extraction.md).

Keep enrichment separate from the inventory transaction. Failure to retrieve
specifications must not undo a valid stock addition or encourage retrying that
addition. Scheduling the lookup synchronously or as a durable job is a deployment
choice; an in-process background task is not a durability guarantee.

Present committed and proposed values distinctly. Let the user inspect evidence
and accept or reject changes through the shared approval policy. Acceptance must
persist the approved values and their provenance together. If a user edits a
proposal, preserve that distinction rather than attributing their wording to the
source. Search and ordinary inventory export concern committed data.

Do not backfill invented provenance for historical records. Removing a supplier
integration must not erase accepted provenance or authoritative inventory.

## Tradeoffs and open questions

Model extraction adds provider cost, latency, and nondeterminism. Review adds user
work. These costs are justified only if correct, useful proposals improve over
simpler retrieval and extraction. Measure exact matches, ambiguity, and failed
retrieval separately; a filled record is not the definition of success.

Model/provider selection, discovery service, retrieval budgets, and durable
source retention remain open. Do not select them indirectly through a fixture.
Start from supplied-source extraction to isolate interpretation from discovery,
then evaluate the end-to-end path. The [acceptance cases](../../evaluation/enrichment/acceptance.json)
are executable data for that work, not a prose report of achieved reliability.
