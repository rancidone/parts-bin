# Parts Bin — Discovery Brief

## Problem Statement
A solo electronics hobbyist needs to track a personal parts inventory — primarily AliExpress passives, transistors, and ICs — across add and lookup operations. The current state is untracked piles. The goal is a lightweight system where ingesting a new part and querying existing stock are fast and low-friction.

## Intended Outcomes
- Ingest a part (by photo, text, or both) and have it committed to inventory with correct value, package, and quantity
- Query inventory in natural language and get a reliable, deterministic answer
- Confidence-gated ingestion: when part ID is uncertain, the system asks rather than guessing

## Constraints
- Single user, no auth or multi-tenancy required
- No location tracking needed
- Decrement on use is a stretch goal, not in scope for initial design
- Inventory queries must use deterministic lookup, not semantic/embedding search — the LLM parses query intent into structured attributes, then a deterministic database query executes against stored records
- External part lookup (LCSC primary, Digikey fallback) applies to parts with resolvable part numbers: discretes (transistors, diodes) and ICs. Passives (resistors, caps, inductors) are stored by value + package only, no external lookup needed.

## Assumptions
- AliExpress labels are often ambiguous; photo + text clarification is the normal ingestion path for anything non-obvious
- Inventory scale: hundreds of SKUs, not thousands
- The LLM-parse → deterministic-execute pattern applies to both ingestion and query: LLM extracts structured attributes, user confirms on low confidence, deterministic read/write executes

## Risks / Edge Cases
- Part identification confidence threshold is undefined — needs a working heuristic for when to ask vs. commit
- Duplicate detection: adding more of a part you already have should increment, not create a second entry
- Query normalization: structured parsing must reliably normalize value representations (e.g. "10kohm" vs "10k") to match stored entries

## Open Questions
- None blocking framing
