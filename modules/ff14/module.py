"""FF14 command handlers assembled from installed extension services."""

from __future__ import annotations

from yomihime_sdk.api.contexts import InvocationView
from yomihime_sdk.api.display import DisplayDocument, Privacy, TextBlock
from yomihime_sdk.api.results import CapabilityResult, ResultStatus
from yomihime_sdk.api.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    ModuleServices,
)
from yomihime_sdk.api.storage import JsonObject

from .config import FF14ConfigSnapshot
from .features.calendar import CalendarCollector, CalendarQuery
from .features.calendar_evaluator import CalendarDailySummaryEvaluator
from .features.calendar_subscriptions import CalendarSubscriptionHandler
from .features.fflogs import FFLogsCharacterLookup, FFLogsOutputPercentiles
from .features.items import ItemLookup
from .features.market_handler import MarketQueryHandler

CAPABILITY_STATUS = "status"
CAPABILITY_ITEM_LOOKUP = "item.lookup"
CAPABILITY_MARKET_QUERY = "ff14.market.query"
CAPABILITY_FFLOGS_CHARACTER = "ff14.logs.character"
CAPABILITY_FFLOGS_OUTPUT = "ff14.logs.output_percentile"
CAPABILITY_CALENDAR_QUERY = "ff14.calendar.query"
CAPABILITY_CALENDAR_SUBSCRIPTION_CREATE = "ff14.calendar.subscription.create"
CAPABILITY_CALENDAR_SUBSCRIPTION_LIST = "ff14.calendar.subscription.list"
CAPABILITY_CALENDAR_SUBSCRIPTION_UPDATE = "ff14.calendar.subscription.update"
CAPABILITY_CALENDAR_SUBSCRIPTION_CANCEL = "ff14.calendar.subscription.cancel"
CAPABILITY_IDS = (
    CAPABILITY_STATUS,
    CAPABILITY_ITEM_LOOKUP,
    CAPABILITY_MARKET_QUERY,
    CAPABILITY_FFLOGS_CHARACTER,
    CAPABILITY_FFLOGS_OUTPUT,
    CAPABILITY_CALENDAR_QUERY,
    CAPABILITY_CALENDAR_SUBSCRIPTION_CREATE,
    CAPABILITY_CALENDAR_SUBSCRIPTION_LIST,
    CAPABILITY_CALENDAR_SUBSCRIPTION_UPDATE,
    CAPABILITY_CALENDAR_SUBSCRIPTION_CANCEL,
)


class Factory:
    """Create this package's handlers from a verified service bundle."""

    async def create(self, services: ModuleServices) -> "FF14Module":
        if not isinstance(services, ModuleServices):
            raise TypeError("ModuleServices are required")
        FF14ConfigSnapshot.from_values((await services.config.current()).values)
        return FF14Module(services)


class FF14Module:
    """Assemble item, FFLogs, and calendar capabilities."""

    def __init__(
        self, services: ModuleServices, *, config: FF14ConfigSnapshot | None = None
    ) -> None:
        self._started = False
        self._market = MarketQueryHandler(services)
        self._handlers = ModuleHandlers(
            capabilities={
                CAPABILITY_STATUS: _StatusHandler(),
                CAPABILITY_ITEM_LOOKUP: ItemLookup(services),
                CAPABILITY_MARKET_QUERY: self._market,
                CAPABILITY_FFLOGS_CHARACTER: FFLogsCharacterLookup(services),
                CAPABILITY_FFLOGS_OUTPUT: FFLogsOutputPercentiles(services),
                CAPABILITY_CALENDAR_QUERY: CalendarQuery(services, config=config),
                CAPABILITY_CALENDAR_SUBSCRIPTION_CREATE: CalendarSubscriptionHandler(
                    services, "subscribe", config=config
                ),
                CAPABILITY_CALENDAR_SUBSCRIPTION_LIST: CalendarSubscriptionHandler(
                    services, "list"
                ),
                CAPABILITY_CALENDAR_SUBSCRIPTION_UPDATE: CalendarSubscriptionHandler(
                    services, "update"
                ),
                CAPABILITY_CALENDAR_SUBSCRIPTION_CANCEL: CalendarSubscriptionHandler(
                    services, "cancel"
                ),
            },
            collectors={"ff14.calendar.collect": CalendarCollector(services)},
            evaluators={"ff14.calendar.daily_summary": CalendarDailySummaryEvaluator()},
        )

    def handlers(self) -> ModuleHandlers:
        return self._handlers

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False
        self._market.close()

    async def check_health(self) -> HealthReport:
        if not self._started:
            default = CapabilityHealth(HealthStatus.UNAVAILABLE, "module_not_started")
            capabilities = {capability: default for capability in CAPABILITY_IDS}
        else:
            default = CapabilityHealth(HealthStatus.AVAILABLE)
            capabilities = {capability: default for capability in CAPABILITY_IDS}
            capabilities[CAPABILITY_FFLOGS_OUTPUT] = CapabilityHealth(
                HealthStatus.UNAVAILABLE,
                "FFLogs \u7edf\u8ba1\u9875\u9762\u5f53\u524d\u8fd4\u56de HTTP 403\uff0c\u9875\u9762\u6570\u636e\u683c\u5f0f\u5c1a\u672a\u9a8c\u8bc1\u3002",
            )
        return HealthReport(capabilities=capabilities)


class _StatusHandler:
    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del context, parameters
        document = DisplayDocument(
            title="FF14 \u6a21\u5757\u72b6\u6001",
            subject="\u53ea\u8bfb\u8fd0\u884c\u8bca\u65ad",
            ordered_blocks=(
                TextBlock(
                    "\u7269\u54c1\u67e5\u8be2\u3001FFLogs \u516c\u5f00\u89d2\u8272\u6218\u7ee9\u548c\u6d3b\u52a8\u65e5\u5386\u67e5\u8be2\u5df2\u63a5\u5165\u3002"
                    "FFLogs OAuth \u5ba2\u6237\u7aef\u51ed\u636e\u6309\u533a\u57df\u5206\u522b\u52a0\u5bc6\u914d\u7f6e\uff1b\u672a\u914d\u7f6e\u65f6\uff0c\u7269\u54c1\u548c\u65e5\u5386\u4ecd\u53ef\u4f7f\u7528\u3002"
                ),
                TextBlock(
                    "FFLogs \u8f93\u51fa\u7edf\u8ba1\u9875\u9762\u5f53\u524d\u56e0 HTTP 403 \u6682\u4e0d\u53ef\u7528\u3002"
                    "\u65e5\u5386\u6bcf\u65e5\u6458\u8981\u9700\u663e\u5f0f\u8ba2\u9605\uff1b\u91c7\u96c6\u95f4\u9694\u4e3a 15 \u5206\u949f\uff0c\u901a\u5e38\u4f1a\u6709\u6700\u591a\u4e00\u4e2a\u91c7\u96c6\u5468\u671f\u7684\u5ef6\u8fdf\uff0c"
                    "\u4e0d\u627f\u8bfa\u5728\u8bbe\u5b9a\u65f6\u523b\u7cbe\u51c6\u9001\u8fbe\u3002"
                ),
            ),
            sources=("FF14 module status",),
            privacy=Privacy.PUBLIC,
        )
        return CapabilityResult(
            result_id="ff14-module-status",
            status=ResultStatus.SUCCESS,
            document=document,
            privacy=Privacy.PUBLIC,
        )


__all__ = ["Factory", "FF14Module"]
