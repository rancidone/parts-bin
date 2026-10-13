# 0019: Batch accepted specification reads for candidate searches

Status: Accepted

## Context

Electrical search evaluates all category candidates to report full match and
incomplete counts before returning a bounded page. Opening a separate SQLite
connection for each candidate adds read overhead that grows with stock size.
The offline inventory-scale runner exposes this work separately from response
sizes and model behavior.

## Decision

Add a typed repository operation that reads accepted facts for specified part
IDs. Search retrieves those facts together, then uses the existing domain
comparison and pagination rules. SQLite uses bounded parameterized queries in
one read snapshot, or the caller's existing unit of work. Missing entries mean
no accepted facts; pending reviews are excluded.

Keep candidate discovery and electrical evaluation in the domain service. Use
measured requirements before introducing indexed specification queries or a
new persistence model.

## Consequences

Batching removes per-candidate connection overhead and preserves source evidence,
qualifiers, assertion handling, counts, and ordering. Candidate evaluation still
scales with the number of records, and later pages repeat evaluation against
current committed stock. Every future storage adapter must implement the batch
read contract. No schema migration or inventory rewrite is required.
