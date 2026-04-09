# Parts Bin — Discovery Migration

## What Happened
- Parts-bin had a legacy discovery artifact under `docs/discovery/parts-bin/current/` and `docs/discovery/parts-bin/checkpoints/`.
- Migrated to the new format by persisting via `discovery_write_draft`. New format writes a single flat file at `docs/discovery/parts-bin.md`.
- During cleanup, `docs/discovery/` was deleted prematurely (before confirming the new artifact's on-disk path was separate). Restored by re-running `discovery_write_draft`. Final checkpoint: `8a16587ca54e4169b70db7570782f523`.

## Current State
- Discovery artifact: `docs/discovery/parts-bin.md`
- Status: `accepted`
- Readiness: `high`
- No blocking gaps. No open questions.

## Brief Summary
Solo hobbyist parts inventory manager. Core operations: ingest (photo/text → LLM extract → confirm → commit) and query (natural language → LLM parse → deterministic DB lookup). LCSC primary / Digikey fallback for discretes and ICs; passives by value+package only. Single user, no auth, no location, no semantic search. Decrement is a stretch goal.

## Next
Run `discovery_to_design` to open the Design stage.
