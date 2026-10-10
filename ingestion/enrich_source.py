"""Operator entry point for one supplied-source enrichment and pending review."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import tomllib

import httpx

from domain import GetPartRequest, PartsBinService
from domain.errors import DomainError
from ingestion.supplied_source import EnrichmentError, ResultCache, enrich, review_result


async def run(args: argparse.Namespace) -> dict:
    with args.config.open("rb") as file:
        config = tomllib.load(file)
    db_path = Path(config.get("db", {}).get("path", "data/parts.db"))
    if not db_path.is_file():
        raise EnrichmentError("Inventory database must already exist")
    service = PartsBinService(db_path)
    part = service.get(GetPartRequest(args.part_id))
    if not part.part_number:
        raise EnrichmentError("Part needs an exact part number before enrichment")
    if part.id in service.list_pending_reviews():
        raise EnrichmentError("Resolve the existing review before starting another enrichment")
    openai = config.get("agent", {}).get("openai", {})
    candidate = await enrich(part.part_number, part.manufacturer, args.source_url,
        api_key=openai.get("api_key", ""), model=args.model,
        cache=ResultCache(db_path), refresh=args.refresh)
    result = review_result(candidate)
    if result["chosen_updates"]:
        service.stage_enrichment(part, result["chosen_updates"], result["durable_provenance"])
    return {**candidate, "part_id": part.id, "review_staged": bool(result["chosen_updates"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part_id", type=int)
    parser.add_argument("source_url", help="Supplied manufacturer PDF URL; no web discovery")
    parser.add_argument("--model", required=True, help="Explicit OpenAI model; uncached calls are billable")
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("PARTS_BIN_CONFIG", "config.toml")))
    parser.add_argument("--refresh", action="store_true", help="Bypass a fresh result; can incur another model call")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except (EnrichmentError, DomainError) as exc:
        parser.exit(1, f"Enrichment failed: {exc}. No proposal applied.\n")
    except (OSError, ValueError, TimeoutError, httpx.HTTPError) as exc:
        # Provider errors may include request details; do not print bodies or keys.
        parser.exit(1, f"Enrichment failed ({type(exc).__name__}); no proposal applied.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
