"""Explicit calendar subscription management through the SDK operations port."""

from __future__ import annotations

import re
from collections.abc import Mapping

from yomihime_sdk.api.contexts import (
    InvocationOrigin,
    InvocationView,
)
from yomihime_sdk.api.display import DisplayDocument, Privacy, TextBlock
from yomihime_sdk.api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from yomihime_sdk.api.services import ModuleServices
from yomihime_sdk.api.storage import JsonObject
from yomihime_sdk.api.subscriptions import SubscriptionRequest, SubscriptionView

from .calendar import normalize_region
from .calendar_evaluator import (
    DEFAULT_LOCAL_TIME,
    DEFAULT_TIMEZONE,
    FILTER_KIND,
    SUMMARY_TYPE_ID,
    load_timezone,
    parse_local_time,
)

_ACTIONS = frozenset(("subscribe", "list", "update", "cancel"))
_ID_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LIST_PAGE_SIZE = 10
_MAX_LIST_ROWS = 100
_MAX_LIST_ROWS = 100


class CalendarSubscriptionHandler:
    """One command action wrapper; authority is enforced by SDK operations."""

    def __init__(self, services: ModuleServices, action: str) -> None:
        if action not in _ACTIONS:
            raise ValueError("unknown calendar subscription action")
        self._services = services
        self._action = action

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        if not _is_direct_command(context):
            return _error(ErrorCode.AUTH_REQUIRED, "请在本人私聊中管理日历订阅。")
        if not isinstance(parameters, Mapping):
            return _error(ErrorCode.PARAMETER_ERROR, "订阅参数无效。")
        if self._action == "subscribe":
            return await self._subscribe(context, parameters)
        if self._action == "list":
            return await self._list(context, parameters)
        if self._action == "update":
            return await self._update(context, parameters)
        return await self._cancel(context, parameters)

    async def _subscribe(
        self, context: InvocationView, parameters: Mapping[str, object]
    ) -> CapabilityResult:
        if (
            set(parameters) - {"region", "timezone", "time"}
            or "region" not in parameters
        ):
            return _error(ErrorCode.PARAMETER_ERROR, "请提供日历区域及可选时区、时间。")
        try:
            region = normalize_region(parameters["region"])
            timezone_name = _timezone(parameters.get("timezone", DEFAULT_TIMEZONE))
            local_time = _local_time(parameters.get("time", DEFAULT_LOCAL_TIME))
        except ValueError:
            return _error(
                ErrorCode.PARAMETER_ERROR,
                "区域、IANA 时区或每日时间无效；时间格式为 HH:MM。",
            )
        request = SubscriptionRequest(
            type_id=SUMMARY_TYPE_ID,
            collector_parameters={"region": region},
            filters=_filters(region, timezone_name, local_time),
            notification_mode="instant",
            digest_schedule=None,
        )
        try:
            view = await self._services.subscriptions.create_request(context, request)
        except Exception:
            return _operation_error()
        return _subscription_receipt(view, "已创建日历每日摘要订阅。")

    async def _list(
        self, context: InvocationView, parameters: Mapping[str, object]
    ) -> CapabilityResult:
        if set(parameters) - {"page"}:
            return _error(
                ErrorCode.PARAMETER_ERROR,
                "\u8ba2\u9605\u5217\u8868\u53c2\u6570\u65e0\u6548\u3002",
            )
        page = parameters.get("page", 1)
        if type(page) is not int or not 1 <= page <= _MAX_LIST_ROWS // _LIST_PAGE_SIZE:
            return _error(
                ErrorCode.PARAMETER_ERROR,
                "page \u9700\u4e3a 1 \u5230 10 \u7684\u6574\u6570\u3002",
            )
        try:
            views = await self._services.subscriptions.list_current(context)
        except Exception:
            return _operation_error()
        rows = tuple(view for view in views if _is_calendar_view(view))
        page_count = max(1, (len(rows) + _LIST_PAGE_SIZE - 1) // _LIST_PAGE_SIZE)
        if page > page_count:
            return _error(
                ErrorCode.PARAMETER_ERROR,
                f"page \u8d85\u51fa\u5f53\u524d\u8303\u56f4\uff0c\u8bf7\u4f7f\u7528 1 \u5230 {page_count}\u3002",
            )
        start = (page - 1) * _LIST_PAGE_SIZE
        selected = rows[start : start + _LIST_PAGE_SIZE]
        blocks = [
            TextBlock(
                f"\u5f53\u524d\u6709 {len(rows)} \u4e2a\u672c\u4eba\u65e5\u5386\u8ba2\u9605\u3002\u7b2c {page}/{page_count} \u9875\uff08\u6bcf\u9875\u6700\u591a {_LIST_PAGE_SIZE} \u6761\uff09\u3002"
            )
        ]
        if not rows:
            blocks.append(
                TextBlock(
                    "\u5f53\u524d\u6ca1\u6709\u65e5\u5386\u6bcf\u65e5\u6458\u8981\u8ba2\u9605\u3002"
                )
            )
        else:
            for view in selected:
                filters = view.filters
                blocks.append(
                    TextBlock(
                        f"\u8ba2\u9605 {view.subscription_id}\uff08revision {view.revision}\uff09\uff1a"
                        f"{_region_label(str(filters['region']))}\uff0c"
                        f"{filters['timezone']}\uff0c\u6bcf\u5929 {filters['time']}\u3002"
                    )
                )
        return _private_result(
            "ff14-calendar-subscriptions",
            "\u65e5\u5386\u8ba2\u9605\u5217\u8868",
            tuple(blocks),
        )

    async def _update(
        self, context: InvocationView, parameters: Mapping[str, object]
    ) -> CapabilityResult:
        if set(parameters) - {
            "subscription_id",
            "expected_revision",
            "timezone",
            "time",
        }:
            return _error(ErrorCode.PARAMETER_ERROR, "订阅更新参数无效。")
        subscription_id, expected_revision = _target(parameters)
        if subscription_id is None:
            return _error(ErrorCode.PARAMETER_ERROR, "请提供订阅 ID 和当前 revision。")
        if "timezone" not in parameters and "time" not in parameters:
            return _error(ErrorCode.PARAMETER_ERROR, "请至少修改时区或每日时间。")
        try:
            views = await self._services.subscriptions.list_current(context)
            current = _find_calendar_view(views, subscription_id)
        except Exception:
            return _operation_error()
        if current is None or current.revision != expected_revision:
            return _not_current()
        old_filters = current.filters
        region = str(old_filters["region"])
        try:
            timezone_name = _timezone(
                parameters.get("timezone", old_filters["timezone"])
            )
            local_time = _local_time(parameters.get("time", old_filters["time"]))
        except (KeyError, ValueError):
            return _error(
                ErrorCode.PARAMETER_ERROR,
                "IANA 时区或每日时间无效；时间格式为 HH:MM。",
            )
        request = SubscriptionRequest(
            type_id=SUMMARY_TYPE_ID,
            collector_parameters={"region": region},
            filters=_filters(region, timezone_name, local_time),
            notification_mode="instant",
            digest_schedule=None,
            subscription_id=subscription_id,
            expected_revision=expected_revision,
        )
        try:
            updated = await self._services.subscriptions.revise_request(
                context, request
            )
        except Exception:
            return _not_current()
        return _subscription_receipt(updated, "已更新日历订阅设置。")

    async def _cancel(
        self, context: InvocationView, parameters: Mapping[str, object]
    ) -> CapabilityResult:
        if set(parameters) != {"subscription_id", "expected_revision"}:
            return _error(ErrorCode.PARAMETER_ERROR, "请提供订阅 ID 和当前 revision。")
        subscription_id, expected_revision = _target(parameters)
        if subscription_id is None:
            return _error(ErrorCode.PARAMETER_ERROR, "订阅 ID 或 revision 无效。")
        try:
            views = await self._services.subscriptions.list_current(context)
            current = _find_calendar_view(views, subscription_id)
        except Exception:
            return _operation_error()
        if current is None or current.revision != expected_revision:
            return _not_current()
        try:
            await self._services.subscriptions.cancel(
                context,
                subscription_id,
                expected_revision=expected_revision,
            )
        except Exception:
            return _not_current()
        return _private_result(
            "ff14-calendar-subscription-cancelled",
            "日历订阅已取消",
            (TextBlock("已取消本人日历每日摘要订阅。"),),
        )


def _is_direct_command(context: object) -> bool:
    return bool(
        isinstance(context, InvocationView)
        and context.origin is InvocationOrigin.COMMAND
        and context.actor_id
        and context.adapter_id
        and context.conversation_id
    )


def _filters(region: str, timezone_name: str, local_time: str) -> dict[str, str]:
    return {
        "kind": FILTER_KIND,
        "region": region,
        "timezone": timezone_name,
        "time": local_time,
    }


def _timezone(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("timezone must be text")
    load_timezone(value)
    return value


def _local_time(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("time must be text")
    parse_local_time(value)
    return value


def _target(parameters: Mapping[str, object]) -> tuple[str | None, int | None]:
    subscription_id = parameters.get("subscription_id")
    revision = parameters.get("expected_revision")
    if (
        not isinstance(subscription_id, str)
        or not subscription_id
        or len(subscription_id) > 256
        or _ID_CONTROL.search(subscription_id)
        or type(revision) is not int
        or revision < 1
    ):
        return None, None
    return subscription_id, revision


def _is_calendar_view(view: object) -> bool:
    if not isinstance(view, SubscriptionView):
        return False
    filters = view.filters
    try:
        if filters.get("kind") != FILTER_KIND or filters.get("region") not in (
            "cn",
            "global",
        ):
            return False
        _timezone(filters.get("timezone", DEFAULT_TIMEZONE))
        _local_time(filters.get("time", DEFAULT_LOCAL_TIME))
        return True
    except ValueError:
        return False


def _find_calendar_view(
    views: tuple[SubscriptionView, ...], subscription_id: str
) -> SubscriptionView | None:
    return next(
        (
            view
            for view in views
            if view.subscription_id == subscription_id and _is_calendar_view(view)
        ),
        None,
    )


def _region_label(region: str) -> str:
    return "国服" if region == "cn" else "国际服"


def _subscription_receipt(view: SubscriptionView, message: str) -> CapabilityResult:
    if not isinstance(view, SubscriptionView) or not _is_calendar_view(view):
        return _operation_error()
    filters = view.filters
    return _private_result(
        "ff14-calendar-subscription-receipt",
        "日历订阅设置",
        (
            TextBlock(message),
            TextBlock(
                f"订阅 {view.subscription_id}（revision {view.revision}）："
                f"{_region_label(str(filters['region']))}，"
                f"{filters['timezone']}，每天 {filters['time']}。"
            ),
        ),
    )


def _private_result(
    result_id: str, subject: str, blocks: tuple[TextBlock, ...]
) -> CapabilityResult:
    document = DisplayDocument(
        title="FF14 日历订阅",
        subject=subject,
        ordered_blocks=blocks,
        sources=("FF14 calendar subscription operations",),
        privacy=Privacy.PRIVATE,
    )
    return CapabilityResult(
        result_id=result_id,
        status=ResultStatus.SUCCESS,
        document=document,
        privacy=Privacy.PRIVATE,
    )


def _not_current() -> CapabilityResult:
    return _error(
        ErrorCode.UPSTREAM_ERROR,
        "订阅不存在、revision 已变化或当前不可管理；请重新查看本人订阅列表。",
    )


def _operation_error() -> CapabilityResult:
    return _error(ErrorCode.UPSTREAM_ERROR, "日历订阅服务暂不可用。")


def _error(code: ErrorCode, message: str) -> CapabilityResult:
    return CapabilityResult(
        result_id="ff14-calendar-subscription-error",
        status=ResultStatus.ERROR,
        privacy=Privacy.PRIVATE,
        error=ErrorDetail(code, message),
    )


__all__ = ["CalendarSubscriptionHandler"]
