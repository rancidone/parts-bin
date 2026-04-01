# Parts Bin

Electronics parts bin manager. Uses an LLM to add, remove, and search inventory via natural language or photos. Photos contain part number labels; the app performs Digikey API lookups to fetch specs.

## Stack
- TBD — update this as the stack is chosen

## Key Concepts
- **Inventory**: parts with quantity, location, and spec metadata
- **Ingestion**: natural language commands or photo uploads → part identification → Digikey lookup → inventory update
- **Search**: natural language queries resolved against inventory + Digikey specs

## Dev Notes
- Prefer simple, direct implementations; avoid speculative abstractions
