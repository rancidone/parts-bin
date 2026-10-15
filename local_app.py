"""Local installation composition, invoked explicitly by the ASGI server."""

import os
import tomllib
from pathlib import Path

import log
from agent_runtime import OpenAIResponsesTransport
from application import create_services
from db.postgres import PostgresPartsBinRepository, PostgresConfig
from ingestion.datasheet import DatasheetFetcher
from ingestion.lookup import fetch_specs_detailed
from server import create_app as create_http_app
from tools.inventory_mcp import MCPConfig


def create_local_services():
    config_path = Path(os.environ.get("PARTS_BIN_CONFIG", Path(__file__).parent / "config.toml"))
    if not config_path.is_file():
        raise RuntimeError(f"config.toml not found at {config_path}")
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)
    log.init()
    repository = PostgresPartsBinRepository(PostgresConfig(**config["db"]))
    agent_config = config.get("agent", {})
    conversations = repository.conversations
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
                                   model=openai.get('model', 'gpt-5.6'), cache=repository.cache),
                               agent_configured=bool(openai.get("api_key")),
                               background_responses=agent_config.get("background_responses", False))
    return services, config


def create_app():
    services, config = create_local_services()
    openai = config.get('agent', {}).get('openai', {})
    mcp = config.get('mcp', {})
    mcp_config = MCPConfig(token=mcp['token'], public_url=mcp['public_url']) if mcp.get('enabled', False) else None
    return create_http_app(services, ui_dist_path=Path(__file__).parent / "ui" / "dist",
                           webhook_secret=openai.get('webhook_secret'), mcp_config=mcp_config)
