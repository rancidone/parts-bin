"""Local installation composition, invoked explicitly by the ASGI server."""

import os
import tomllib
from pathlib import Path

import log
from agent_runtime import OpenAIResponsesTransport
from application import create_services
from db.conversations import SQLiteConversationRepository
from db.repository import SQLitePartsBinRepository
from db.enrichment_cache import SQLiteEnrichmentCache
from ingestion.datasheet import DatasheetFetcher
from ingestion.lookup import fetch_specs_detailed
from server import create_app as create_http_app


def create_app():
    config_path = Path(os.environ.get("PARTS_BIN_CONFIG", Path(__file__).parent / "config.toml"))
    if not config_path.is_file():
        raise RuntimeError(f"config.toml not found at {config_path}")
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)
    log.init()
    database = Path(config["db"]["path"])
    repository = SQLitePartsBinRepository(database)
    agent_config = config.get("agent", {})
    conversations = SQLiteConversationRepository(agent_config.get("conversation_db_path", str(database)))
    openai = agent_config.get("openai", {})
    digikey = config.get("digikey", {})
    supplier_credentials = (
        {"client_id": digikey["client_id"], "client_secret": digikey["client_secret"]}
        if digikey.get("client_id") else None
    )

    async def fetcher(part_number: str) -> dict:
        return await fetch_specs_detailed(part_number, supplier_credentials,
                                          search_config=config.get("search"))

    def transport_factory():
        if not openai.get("api_key"):
            raise RuntimeError("OpenAI API is not configured (agent.openai.api_key)")
        return OpenAIResponsesTransport(api_key=openai["api_key"], model=openai.get("model", "gpt-5.6"),
                                       base_url=openai.get("base_url", "https://api.openai.com/v1"))

    services = create_services(repository, conversations, transport_factory=transport_factory,
                               spec_fetcher=fetcher,
                               datasheet_fetcher=DatasheetFetcher(api_key=openai.get('api_key', ''),
                                   model=openai.get('model', 'gpt-5.6'), cache=SQLiteEnrichmentCache(database)),
                               agent_configured=bool(openai.get("api_key")))
    return create_http_app(services, ui_dist_path=Path(__file__).parent / "ui" / "dist")
