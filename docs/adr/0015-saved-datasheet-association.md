# 0015: Treat a saved datasheet as the operator's part association

Status: Accepted; supersedes the saved-link identity requirements of [0014](0014-device-identity-in-supplied-datasheets.md).

## Context

Operators attach datasheets to their inventory records. Requiring another
identity confirmation when a record omits a package or shipping suffix makes
that association unnecessarily repetitive and withholds shared electrical facts.

## Decision

When electrical extraction uses the record's saved datasheet URL, accept the
operator's association of document and part. Quote the actual document device
designation internally and preserve the inventory label. Extract common ratings
across package and shipping variants without another override. Omit facts that
depend on an unresolved electrical grade rather than blocking all shared facts.

The association is an operator assertion; electrical values still require source
passages, qualifiers, validation, and review. Clearly unrelated documents and
manufacturer contradictions remain failures. Supplier-discovered URLs that have
not been saved still require independent identity evidence. Metadata extraction
retains its exact-identity contract. Separate association modes in the disposable
cache and version the extraction policy.

## Consequences

Saving a link is sufficient to start electrical review without another per-part
setting. It does not rename parts, select unsupported variant ratings, accept
facts, or change stock. Model interpretation of shared ratings still requires
review and representative live evaluation; mocked cases verify the contract only.
