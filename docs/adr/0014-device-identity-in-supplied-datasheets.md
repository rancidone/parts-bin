# 0014: Accept explicit device identity alongside shipping codes

Status: Accepted

## Context

A manufacturer datasheet can name one electrical device in its title and list
a longer shipping code separately. Requiring the inventory's unsuffixed device
name to be an ordering-table code can withhold otherwise supported ratings.
BC517 and the BC517-D74Z ordering entry illustrate this distinction.

## Decision

Permit a standalone device title to establish an unsuffixed inventory identity
when the source explicitly assigns the ratings to that device. Preserve the
inventory identity; do not append a shipping suffix. Family titles spanning
electrical grades or variants still require clarification. A marking alone or
a substring of a longer code remains insufficient.

Treat Unicode dashes as suffix boundaries when checking literal identity,
selecting source context and identifying relevant pages. Do not normalize away
or substitute supplied suffixes. Version the extraction policy so previously
cached clarification results do not suppress extraction under this rule.

## Consequences

Device-level ratings can proceed through the existing evidence and approval
flow without changing stock identity. Source interpretation remains a model
task subject to review; literal quotation validation cannot establish that a
title represents one electrical variant. The live evaluation includes BC517
alongside the existing exact gain-grade and ambiguous-identity cases.
