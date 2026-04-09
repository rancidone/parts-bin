# Implementation TODO

## Recent Design Units

### 1. Enrichment runtime foundation

- [x] Replace the flat lookup result with a structured enrichment attempt model.
- [x] Represent ordered source attempts, field candidates, chosen updates, and final outcome codes.
- [x] Preserve provider diagnostics without depending on LLM response text for user-visible status.
- [x] Keep the old `fetch_specs` compatibility path only where needed during migration.

### 2. Durable provenance storage

- [x] Add schema support for persisted enrichment provenance alongside `parts`.
- [x] Store one provenance record per persisted field update.
- [x] Track source tier, source kind, locator, extraction method, confidence marker, and conflict status.
- [x] Make repeated enrichments replace stale provenance for fields they overwrite.

### 3. Primary API normalization and reconciliation

- [x] Normalize LCSC and DigiKey API responses into source records instead of raw field dicts.
- [x] Detect blocking disagreement on `part_number`, `package`, `manufacturer`, and categorization.
- [x] Reconcile non-conflicting high-authority fields into chosen updates.
- [x] Return explicit outcomes: `saved`, `no_match`, `incomplete`, `conflict`, `timeout`, `failed`, `needs_confirmation`.

### 4. API-derived page extraction

- [x] Fetch only API-derived product page URLs.
- [x] Classify fetched content before extraction.
- [x] Add dedicated DigiKey and LCSC HTML extractors with structured-first parsing.
- [x] Emit candidate values with bounded evidence snippets and warnings.

### 5. API-derived PDF extraction

- [x] Follow PDF URLs from API payloads or API-derived pages only.
- [x] Add bounded PDF metadata extraction for inventory-relevant fields.
- [x] Emit page references and extraction snippets for recovered candidates.
- [ ] Keep ambiguous family-level or variant-level PDF data out of automatic updates.

### 6. Server and ingestion integration

- [x] Update lookup actions to persist reconciled updates plus provenance.
- [x] Surface conflict/incomplete/timeout outcomes in server responses.
- [x] Route new-part ingestion through the same enrichment runtime.
- [x] Avoid direct field writes from raw provider payloads.

### 7. Tests

- [x] Add persistence tests for provenance schema and overwrite behavior.
- [x] Add lookup tests for reconciliation, conflicts, and outcome mapping.
- [x] Add server tests for user-visible responses across enrichment outcomes.
- [x] Add extractor tests with fixture HTML/PDF inputs before enabling networked fallback stages.

## First slice

- [x] Define the enrichment attempt/result shape in `ingestion/lookup.py`.
- [x] Add provenance persistence helpers in `db/persistence.py`.
- [x] Update server lookup writes to use chosen updates plus provenance records.
- [x] Backfill tests for the new result contract.
