# 0025: Drain requests during deployment and preserve recoverable status

**Status:** Accepted

## Context

Replacing the single Compose container during a model call can interrupt a chat.
Docker's default stop window is shorter than a normal agent request. An abrupt
worker loss leaves its durable lease until expiry. Treating a rejected takeover
as a failed execution misrepresents recoverable work and obscures the cause.
BOM failures also need diagnostics that distinguish text context from opaque
reasoning state without recording prompts or inventory.

## Decision

Give Uvicorn a bounded graceful shutdown window inside a longer Compose stop
window. Uvicorn stops accepting connections and waits for existing requests;
forced cancellation releases execution ownership while retaining checkpoints and
committed operation outcomes. Keep hard-loss lease protection and explicit resume.
See [Uvicorn shutdown behavior](https://www.uvicorn.org/server-behavior/) and
[Compose stop grace](https://docs.docker.com/reference/compose-file/services/#stop_grace_period).

A live lease rejects takeover with a typed temporary busy condition and a retry
bound. End the resume attempt with a nonterminal busy status tied to the original
execution, preserving its recovery and approval controls. A late busy event must
not reopen an execution already known to have finished. Record deferral separately
from runtime failure.

Retain the current BOM text and independent reasoning budgets and native response
items; do not drop function pairs or invent summaries to get below a limit.
Record model-request byte counts, tool/exchange counts and omitted-result counts.
Budget and incomplete-response failures emit their exact stable code, and forced
cancellation emits an interrupted outcome. These diagnostics contain no raw text,
images, tool values, reasoning content or exception messages.

## Consequences

An upgrade can take longer while active requests finish. A single-container
installation can temporarily reject new connections during shutdown; this does
not provide continuous availability. Work exceeding the shutdown bound and hard
host/process loss still require explicit recovery. Resume can repeat an unsaved
model call, while durable stock outcomes prevent repeated mutations. Telemetry
explains budget pressure but does not measure semantic BOM accuracy.
