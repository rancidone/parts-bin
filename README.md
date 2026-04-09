# Parts Bin

Electronics parts bin manager. Uses an LLM to add, remove, and search inventory via natural language or photos.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Node.js + npm
- A running [llama.cpp](https://github.com/ggerganov/llama.cpp) server
- (Optional) Digikey API credentials for spec lookups

## Setup

```sh
# Install Python dependencies
uv sync

# Install UI dependencies
cd ui && npm install && cd ..
```

## Start / Stop

```sh
./dev.sh          # start API (port 8000) and UI (port 5173)
./dev.sh stop     # stop both
```

- API: http://localhost:8000
- UI:  http://localhost:5173

## Tests

```sh
uv run pytest
```
