# Work to resolve

This is a planning list, not implementation evidence. Remove entries when the
work no longer needs tracking; use code, tests, and run output to inspect behavior.
Decisions and rationale belong in [ADRs](docs/adr/README.md).

Prioritize finding parts through available inventory combinations, and reliable operation on the existing Docker/PostgreSQL installation.
Keep execution compatible with short-lived, replaceable workers;
cloud deployment and provider-specific infrastructure are outside the roadmap.

## Execution measurements

- Evaluate background Responses submission and collection with the intended
  model and workloads on real inventory/photo workloads. Measure submission
  latency, collection cadence, costs and result-expiry behavior within the
  provider's temporary polling window. Keep uncertain submissions distinct from
  safe retrieval retries; do not silently extend provider retention.

## Deferred enrichment measurements

Prioritize product features. Resume paid evaluation only for a concrete user
request or a production failure that justifies its cost; accept incomplete
extraction and keep unsupported fields unknown.

- Extend independently reviewed source cases from actual stock and ingestion
  failures, including marked LED intensity grades, regulator voltage variants,
  incomplete transformer packaging codes, and ambiguous stock.
- Review production overlapping-stock, partial-match and specification-search
  conversations when those cases accumulate; distinguish unresolved row identity
  from missing stock, verify explicit partial-match explanations and inspect
  condition qualifiers in the resulting answers.
- When evaluation is justified, distinguish retrieval and source-selection
  failures, missing facts, server rejections, and qualifier mismatches. Compare
  model interpretation against independent source expectations rather than
  treating schema success as correctness.
- Evaluate scanned-datasheet OCR and automatic source discovery separately when
  concrete requests need them; retain integrations only when reliability justifies
  their complexity and cost.

## Deferred local requirements

- Add derating or coverage beyond explicitly documented operating conditions
  only from concrete source-backed requests. Containment of documented operating
  ranges does not establish behavior outside them or turn test points into guarantees.
- Add typed category attributes for connectors, modules and kits only when
  concrete ingestion failures require structured variant descriptions. Preserve
  separate unnumbered physical groups and rehearse any migration on copies.

Keep [hosting-independent execution](docs/adr/0033-serverless-compatible-self-hosting.md)
and [PostgreSQL persistence](docs/adr/0034-postgresql-only-persistence.md) as architectural
constraints, rather than a cloud migration workstream.
