"""A deterministic, SDK-only module with a public page capability."""

from yomihime_game_link_sdk.display import DisplayDocument, Privacy, TextBlock
from yomihime_game_link_sdk.results import CapabilityResult, FactDocument, ResultStatus
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    ModuleServices,
)


class Factory:
    async def create(self, services: ModuleServices) -> "MinimalModule":
        del services
        return MinimalModule()


class MinimalModule:
    def handlers(self) -> ModuleHandlers:
        return ModuleHandlers(
            capabilities={"status": Status()}, collectors={}, evaluators={}
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def check_health(self) -> HealthReport:
        return HealthReport(
            capabilities={"status": CapabilityHealth(status=HealthStatus.AVAILABLE)}
        )


class Status:
    async def invoke(self, context, parameters) -> CapabilityResult:
        del context, parameters
        return CapabilityResult(
            result_id="minimal-status",
            status=ResultStatus.SUCCESS,
            document=DisplayDocument(
                title="Minimal module",
                subject="Deterministic public status",
                ordered_blocks=(TextBlock("The minimal module is available."),),
                privacy=Privacy.PUBLIC,
            ),
            model_facts=FactDocument(facts={"sample": "minimal", "ready": True}),
            privacy=Privacy.PUBLIC,
        )
