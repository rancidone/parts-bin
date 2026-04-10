# Implementation TODO

## Done

- [x] Refresh stale design docs for ingestion, query, migration, and UI against the structured enrichment model.
- [x] Migrate the legacy `run_ingestion()` caller from synchronous accepted lookup writes to write-first pending-review proposals.
- [x] Extend chat/LLM action handling for structured assistant history, passive-field repair, and multi-record updates.
- [x] Add inventory edit and delete endpoints plus matching UI controls.
- [x] Keep pending review separate from committed inventory during query.
- [x] Clear stale pending review records when a committed manual edit replaces the row state.

## Next

- [ ] Decide whether manual edits should preserve unaffected pending fields or always clear the full proposal set.
- [ ] Decide how much raw extraction evidence should be stored durably versus referenced indirectly from provenance.
- [ ] Decide whether query should eventually expose provenance-aware or pending-review-aware filters as an explicit separate surface.
- [ ] Review whether pending-review provenance should be visible in the inventory UI before acceptance.
