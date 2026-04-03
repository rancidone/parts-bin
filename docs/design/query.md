# Design Unit: Query

## Problem
User asks a natural language question about inventory. System parses it into structured attributes, normalizes values using the same canonical form as write time, executes a deterministic DB lookup, and returns a reliable answer.

## Flow

```
natural language query
  └─ LLM parsing → structured query record (null for unresolved fields)
       └─ normalization (same function as write-time canonicalization)
            └─ deterministic DB lookup (exact match on resolved fields)
                 ├─ match(es) found → return results
                 └─ no match → "not in inventory" (do not guess or approximate)
```

## Normalization

Normalization runs at both write and query time using the **same function** — this is the contract that makes matching reliable.

**Canonical form: normalized string with a fixed suffix table.** Human-readable, unambiguous, straightforward to test.

### Suffix Table

| Domain | Canonical suffixes | Input examples → canonical |
|---|---|---|
| Resistance | r, k, m (mega) | `10R`, `10ohm`, `10` → `10r` |
| | | `2R2`, `2.2ohm` → `2.2r` |
| | | `10k`, `10K`, `10kohm`, `10000` (resistance context) → `10k` |
| | | `5K1`, `5.1k` → `5.1k` |
| | | `1M`, `1Mohm` → `1m` |
| | | `1M5` → `1.5m` |
| Capacitance | p, n, u | `100nF`, `0.1uF`, `100000pF` → `100n` |
| | | `2n2` → `2.2n` |
| Inductance | n, u, m (milli) | `10uH`, `0.01mH` → `10u` |
| | | `4u7` → `4.7u` |

**EIA multiplier-as-decimal notation** (`2R2`, `5K1`, `4u7`) is expanded to explicit decimal form at parse time. The letter signals both the multiplier and the decimal position.

Normalization is case-insensitive. Unit suffixes (ohm, F, H, etc.) are stripped before applying the suffix table.

**Bare integers** (e.g. `10000` with no unit context) are resolved by the LLM parsing step before normalization runs — normalization only operates on values that already have a unit domain resolved.

## Boundaries

- LLM parses query intent into structured fields only. It does not execute the lookup or post-process results.
- Normalization is a pure function shared with the write path — not duplicated logic.
- Lookup is exact match only. No fuzzy matching, no semantic similarity.
- A null field in the query record is treated as a wildcard (match any value for that field), not as a filter that excludes results.
- No match returns a definitive "not in inventory" — the system does not approximate or suggest alternatives.

## Assumptions

- The same normalization function is used at write time and query time. Divergence between the two is a bug.
- Bare integers without unit context are resolved by LLM parsing before reaching normalization.
- EIA multiplier-as-decimal notation will appear on real labels and must be handled correctly.
- Null-as-wildcard is the correct query semantic for unspecified fields.
