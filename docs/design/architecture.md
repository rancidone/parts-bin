---
status: stable
last_updated: 2026-04-12
---
# Parts Bin — Architecture Overview

## System Summary

A single-user local app for managing an electronics parts inventory. The user interacts via chat and photo upload. The LLM extracts structured intent; deterministic code executes all reads and writes. The LLM never drives inventory mutations directly.

## Components

| Component | Responsibility | Design doc |
|---|---|---|
| Configuration | Runtime config for all subsystems via `config.toml` | `configuration.md` |
| Server | FastAPI entry point; routes chat and photo to ingestion or query; streams responses | `server-api.md` |
| LLM client | Structured chat completions via llama.cpp; OpenAI API fallback | `llm-integration.md` |
| Photo pipeline | Resizes and encodes uploaded photos for LLM multimodal input | `photo-pipeline.md` |
| Data model | SQLite: committed parts, accepted provenance, pending review proposals | `data-model-and-persistence.md` |
| Ingestion | LLM extraction → duplicate check → immediate write → async enrichment proposal | `ingestion-enrichment-integration.md` |
| Query | LLM parse → normalize → deterministic lookup → LLM answer over results | `query-runtime-alignment.md` |
| Enrichment pipeline | Source authority chain: distributor APIs → product pages → PDFs → web (human-confirmed) | `fallback-enrichment-pipeline.md` |
| Source extraction | Fetches and parses metadata from API-derived URLs and PDFs | `source-retrieval-and-extraction.md` |
| UI | Chat, inventory browse and enrichment review, settings | `ui-enrichment-review-and-settings.md` |

## Cross-Cutting Patterns

**LLM-parse → deterministic-execute.** The LLM extracts structured attributes from natural language or photos. A deterministic layer normalizes and executes the actual read or write. This applies to both ingestion and query.

**Write-then-enrich.** Ingestion commits the inventory row immediately. Enrichment runs asynchronously and produces proposals; the user accepts or dismisses them. A failed enrichment never rolls back a committed part.

**Source authority.** Enrichment follows a fixed trust chain. Distributor APIs are authoritative. Fallback to scraped pages and PDFs is automatic only when sources are API-derived. Open-web search requires human confirmation before use.
