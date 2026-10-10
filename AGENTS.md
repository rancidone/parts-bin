# Parts Bin agent guide

Parts Bin is an electronics-inventory application with a Python/FastAPI backend,
currently using SQLite persistence, and a React/Vite UI. Support local-first
operation and reproducible cloud deployment into an operator's own account.
Preserve existing inventory, conversations, and provenance.

Its core workflows are inventory management, natural-language or photo-based
ingestion, part-number identification and supplier-spec lookup, and inventory
search using stored parts and their specifications. Prefer simple, direct
implementations over speculative abstractions.

## Architecture direction

- Use the OpenAI Responses API as the single agent interface in both local and
  cloud deployments. Local-first refers to application/data ownership, not a
  separate local-model backend. Do not add provider adapters or runtime pickers
  without an explicit architecture decision.
- Keep inventory rules in a typed domain service. HTTP handlers, agent runtimes,
  and UI code are adapters, not alternate business-logic layers.
- Maintain one typed Parts Bin tool contract for the agent. Models discover
  inventory through narrow tools; never inject the full inventory into prompts.
- Mutations require server-side validation and the shared approval policy. Do
  not add raw SQL tools, generic action/patch envelopes, direct model database
  writes, or compatibility/fallback paths.
- Preserve historical conversation identity; never silently replay a retired
  provider's thread through OpenAI.
- Keep cloud-specific infrastructure and persistence concerns out of domain
  rules. Supplier caches are disposable; inventory and conversation storage
  are authoritative and require explicit migration and recovery procedures.

## Enrichment contract

- Models interpret retrieved manufacturer/distributor evidence; model memory
  is not an authoritative source of specifications.
- Prefer on-demand retrieval and model-assisted extraction over maintaining a
  local bulk supplier catalog. Keep integrations only when measured reliability
  justifies their complexity.
- Preserve exact part identity, including meaningful suffixes. Do not silently
  substitute related parts, infer a package from an ambiguous family name, or
  invent manufacturer, pinout, voltage, or protocol.
- Every proposed factual field needs traceable evidence. Distinguish user
  assertions from independently verified information. Leave unsupported fields
  unknown and ask targeted clarification questions when identity is ambiguous.
- Preserve qualifiers on electrical specifications, especially absolute maximum
  versus operating limits and continuous versus pulsed current.
- Stage changes through the existing review/approval flow. Enrichment must not
  change quantity or silently overwrite committed inventory. Retrieval failure
  and no matching part are different outcomes.
- Evaluate representative successes, failures, and ambiguous inputs. Structural
  checks and recorded fixtures are not evidence of live enrichment quality.

## Cloud deployment

- Target AWS serverless infrastructure provisioned with Terraform. Validate
  service choices against application constraints and official documentation;
  record decisions and tradeoffs in `docs/design/`.
- Keep deployments independent, with one owner per installation. Do not introduce
  tenancy or shared multi-user access control without an explicit scope change.
- Cloud deployments require HTTPS, authentication, secure configuration,
  automated deployment, monitoring, and tested backup/restore.
- Maintain operator documentation for fresh-account setup, expected costs,
  upgrades, rollback, recovery, and teardown. Verify data preservation across
  upgrades and recovery, and measure application performance.

## Repository map

- `server.py`: current FastAPI entry point and API adapters.
- `domain/`: typed inventory rules and domain errors.
- `db/`: current SQLite schema and inventory persistence.
- `agent_runtime/`: gateway, OpenAI transport, approvals, conversation storage,
  and telemetry.
- `tools/`: typed inventory tool registry.
- `ingestion/`, `photo/`: source lookup/extraction and image processing.
- `evaluation/`: recorded agent scenarios and enrichment acceptance checks.
- `ui/`: React/Vite client.
- `docs/README.md`, `docs/design/`: project understanding and design decisions.
- `docs/operations.md`: practical operating and recovery guidance.
- `TODO.md`: upcoming work, not an implementation-status ledger.

## Local development

- Python requires 3.14+ and uses `uv`; the UI uses Node.js and npm.
- Copy `config.example.toml` to the untracked `config.toml` for local services.
  Automated tests use isolated data and mocked providers. OpenAI credentials
  are required for live agent use; supplier credentials are optional.
- Follow `README.md` for local startup commands and `compose.yaml` for container
  configuration.

## Working conventions

- Do not commit credentials or local runtime configuration. Treat `config.toml`,
  databases, telemetry, and uploaded images as local data.
- Prefer focused tests alongside changed code. Run `uv run pytest` for backend
  changes; for UI changes run `npm run lint` and `npm run build` from `ui/`.
- Code defines implemented behavior; executable checks and their run output are
  implementation evidence. Documentation explains decisions, rationale, tradeoffs,
  and useful operating procedures. Do not maintain completion claims, passing
  counts, coverage matrices, or duplicate code/configuration schemas in prose.
- Update tests with behavior changes, and docs when decisions or user procedures
  change. Delete obsolete paths rather than retaining deprecated shims.
- Keep this file focused on durable project instructions. Put task status,
  milestones, and temporary plans in work-tracking documents, not `AGENTS.md`.
