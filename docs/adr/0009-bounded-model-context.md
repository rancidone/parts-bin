# 0009: Bound model context independently of retained data

**Status:** Accepted

## Context

Replaying all conversation prose or a review backlog makes ordinary inventory
requests more expensive as local data grows. Silent truncation can also remove
qualifiers or conceal completed mutations. Source extraction needs the evidence
for an exact ordering variant, rather than every page of a datasheet.

## Decision

Keep authoritative events and provenance intact. Model context retains a bounded,
contiguous suffix of complete conversation turns with an explicit omission notice.
Use UTF-8 byte budgets for history, individual tool results, and total text input,
including instructions and schemas, rather than assume a characters-per-token
ratio. Preserve native function-call pairs and reasoning items within the current
execution. Stop explicitly if that exchange exceeds the total budget.

Oversized tool results remain saved for the UI. The model receives the execution's
success flag and a notice that facts were omitted, with directions to narrow reads
and avoid repeating mutations. Discover pending reviews through filtered pages;
fetch their provenance only for a selected part.

Send photos on the initial model call and ask the model to record observations
and uncertainties as text before tool use. Later visual inspection requires a new
photo. Cap model output, including reasoning; incomplete responses cannot execute
partial calls. See [OpenAI's reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning).

For supplied PDFs, select bounded verbatim windows around exact identity and
ordering/package context. Retain original page numbers, validate every proposed
quote against the supplied windows and original document, and treat absence from
selected text as unresolved identity. Change the extraction policy identity so
cached results from an earlier policy are not reused.

## Consequences

Old conversations remain readable, but references to omitted turns may require
clarification. Broad requests may need narrower queries, smaller pages, or direct
inspection of saved outcomes. No input budget can undo effects already completed;
recovery must preserve the durable execution and approval rules. Byte limits bound
text size rather than precisely predicting billed tokens, and images have separate
preprocessing limits. Recorded tests establish request bounds and preservation;
live extraction and photo quality still require representative evaluation.
