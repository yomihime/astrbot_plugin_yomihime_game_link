"""FF14 command handlers assembled from installed extension services."""

from __future__ import annotations

from yomihime_game_link_sdk.contexts import InvocationView
from yomihime_game_link_sdk.display import Privacy
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    ModuleServices,
)
from yomihime_game_link_sdk.storage import JsonObject

from .config import FF14ConfigSnapshot
from .features.calendar import CalendarCollector, CalendarQuery, CalendarSyncCollector
from .features.calendar_evaluator import CalendarDailySummaryEvaluator
from .features.calendar_snapshots import CalendarSnapshots
from .features.calendar_subscriptions import CalendarSubscriptionHandler
from .features.fflogs import FFLogsCharacterLookup
from .features.items import ItemLookup
from .features.market_handler import MarketQueryHandler
from .features.status_cards import build_status_document

CAPABILITY_STATUS = "status"
CAPABILITY_ITEM_LOOKUP = "item.lookup"
CAPABILITY_ITEM_SELECT = "ff14.item.select"
CAPABILITY_MARKET_QUERY = "ff14.market.query"
CAPABILITY_MARKET_SELECT = "ff14.market.select"
CAPABILITY_FFLOGS_CHARACTER = "ff14.logs.character"
CAPABILITY_CALENDAR_QUERY = "ff14.calendar.query"
CAPABILITY_CALENDAR_SUBSCRIPTION_CREATE = "ff14.calendar.subscription.create"
CAPABILITY_CALENDAR_SUBSCRIPTION_LIST = "ff14.calendar.subscription.list"
CAPABILITY_CALENDAR_SUBSCRIPTION_UPDATE = "ff14.calendar.subscription.update"
CAPABILITY_CALENDAR_SUBSCRIPTION_CANCEL = "ff14.calendar.subscription.cancel"
CAPABILITY_IDS = (
    CAPABILITY_STATUS,
    CAPABILITY_ITEM_LOOKUP,
    CAPABILITY_ITEM_SELECT,
    CAPABILITY_MARKET_QUERY,
    CAPABILITY_MARKET_SELECT,
    CAPABILITY_FFLOGS_CHARACTER,
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
        self._items = ItemLookup(services)
        self._logs = FFLogsCharacterLookup(services)
        calendar_snapshots = CalendarSnapshots(services)
        self._handlers = ModuleHandlers(
            capabilities={
                CAPABILITY_STATUS: _StatusHandler(),
                CAPABILITY_ITEM_LOOKUP: self._items,
                CAPABILITY_ITEM_SELECT: self._items,
                CAPABILITY_MARKET_QUERY: self._market,
                CAPABILITY_MARKET_SELECT: self._market,
                CAPABILITY_FFLOGS_CHARACTER: self._logs,
                CAPABILITY_CALENDAR_QUERY: CalendarQuery(
                    services, config=config, snapshots=calendar_snapshots
                ),
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
            collectors={
                "ff14.calendar.collect": CalendarCollector(
                    services, snapshots=calendar_snapshots
                ),
                "ff14.calendar.sync": CalendarSyncCollector(
                    services, snapshots=calendar_snapshots
                ),
            },
            evaluators={"ff14.calendar.daily_summary": CalendarDailySummaryEvaluator()},
        )

    def handlers(self) -> ModuleHandlers:
        return self._handlers

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False
        self._market.close()
        self._items.close()
        self._logs.close()

    async def check_health(self) -> HealthReport:
        if not self._started:
            default = CapabilityHealth(HealthStatus.UNAVAILABLE, "module_not_started")
            capabilities = {capability: default for capability in CAPABILITY_IDS}
        else:
            default = CapabilityHealth(HealthStatus.AVAILABLE)
            capabilities = {capability: default for capability in CAPABILITY_IDS}
        return HealthReport(capabilities=capabilities)


class _StatusHandler:
    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del context, parameters
        document = build_status_document()
        return CapabilityResult(
            result_id="ff14-module-status",
            status=ResultStatus.SUCCESS,
            document=document,
            privacy=Privacy.PUBLIC,
        )


__all__ = ["Factory", "FF14Module"]
