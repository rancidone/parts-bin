# Architecture decision records

These records capture existing project choices and their rationale, not feature
status. Recording them does not select additional services or change behavior.

- [0001: Keep domain rules independent of hosting and storage](0001-domain-and-storage-boundaries.md)
- [0002: Use one OpenAI Responses interface](0002-openai-responses-interface.md)
- [0003: Commit mutation outcomes with effects and recover explicitly](0003-durable-effects-and-explicit-recovery.md)
- [0004: Enrich on demand from evidence, through review](0004-source-backed-enrichment.md)
- [0005: Keep photos ephemeral and caches separate from authoritative data](0005-storage-and-retention.md)
- [0006: Normalize in the domain without rewriting or merging historical stock](0006-normalization-without-stock-merges.md)
- [0007: Match electrical requirements against reviewed, qualified facts](0007-reviewed-electrical-facts.md)
- [0008: Target AWS and Terraform without selecting a cloud stack yet](0008-deferred-aws-direction.md)

- [0009: Bound model context independently of retained data](0009-bounded-model-context.md)
- [0010: Cite supplied electrical evidence by server-owned passages](0010-supplied-electrical-passages.md)

Use one numbered record per decision with **Status**, **Context**, **Decision**,
and **Consequences**. Do not append implementation progress, test counts, or
current configuration. Correct factual mistakes when necessary; when a decision
changes, add a new ADR and mark the old one superseded with a link.

Keep unresolved proposals in [TODO](../../TODO.md), commands in
[operations](../operations.md), and configuration in
[the sample config](../../config.example.toml).
