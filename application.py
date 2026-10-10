"""Application assembly shared by HTTP hosts and worker entry points."""

from collections.abc import Callable
from dataclasses import dataclass

from agent_runtime import AgentGateway, AgentTelemetry, ApprovalEngine, OpenAIResponsesRuntime
from agent_runtime.runtime import ModelTransport
from agent_runtime.store import ConversationRepository
from domain import PartsBinService
from domain.repositories import PartsBinRepository
from domain.service import SpecFetcher, DatasheetFetcher
from tools import PartsBinToolRegistry


@dataclass(frozen=True)
class ApplicationServices:
    domain: PartsBinService
    gateway: AgentGateway
    agent_configured: bool

    async def close(self) -> None:
        await self.gateway.close()


def create_services(repository: PartsBinRepository, conversations: ConversationRepository, *,
                    transport_factory: Callable[[], ModelTransport],
                    spec_fetcher: SpecFetcher | None = None,
                    datasheet_fetcher: DatasheetFetcher | None = None,
                    telemetry: AgentTelemetry | None = None,
                    agent_configured: bool = True) -> ApplicationServices:
    """Assemble one installation without choosing storage or reading configuration."""
    domain = PartsBinService(repository, spec_fetcher=spec_fetcher, datasheet_fetcher=datasheet_fetcher)
    approvals = ApprovalEngine(repository)
    telemetry = telemetry or AgentTelemetry()

    def make_runtime() -> OpenAIResponsesRuntime:
        return OpenAIResponsesRuntime(transport_factory(), registry=PartsBinToolRegistry(domain),
                                      store=conversations, approvals=approvals, telemetry=telemetry)

    return ApplicationServices(domain, AgentGateway(conversations, make_runtime, telemetry=telemetry),
                               agent_configured)
