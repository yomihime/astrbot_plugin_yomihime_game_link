"""No-op factory ABI example; importing this module has no side effects."""

from yomihime_sdk import HealthReport, ModuleHandlers, ModuleServices


class Factory:
    async def create(self, services: ModuleServices) -> "EmptyModule":
        del services
        return EmptyModule()


class EmptyModule:
    def handlers(self) -> ModuleHandlers:
        return ModuleHandlers(capabilities={}, collectors={}, evaluators={})

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        return HealthReport(capabilities={})
