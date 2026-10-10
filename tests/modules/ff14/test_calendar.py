from __future__ import annotations

import hashlib
import unittest
from collections import deque
from datetime import UTC, datetime
from unittest import mock

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
)

from modules.ff14.config import FF14ConfigSnapshot
from modules.ff14.features.calendar import (
    COLLECTOR_ID,
    DATA_VERSION,
    FALLBACK_SOURCE_ID,
    KEY_VERSION,
    SCHEDULE_SOURCE_ID,
    CalendarCollector,
    CalendarQuery,
    CalendarSourceReader,
    normalize_region,
)
from modules.ff14.features.calendar_evaluator import (
    DEFAULT_LOCAL_TIME,
    DEFAULT_TIMEZONE,
    FILTER_KIND,
    SUMMARY_TYPE_ID,
    calendar_source_spec,
)
from modules.ff14.features.calendar_subscriptions import CalendarSubscriptionHandler
from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.display import (
    DisplayLimits,
    LinksBlock,
    Privacy,
    TableBlock,
)
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import ErrorCode, ResultStatus
from yomihime_game_link_sdk.services import ConfigSnapshot, HttpRequest, HttpResponse
from yomihime_game_link_sdk.storage import OwnerScope
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    CollectionView,
    NormalizedInput,
    ObservationCompleteness,
    SubscriptionRequest,
    SubscriptionView,
)


class _ConfigView:
    def __init__(self, values=None):
        self.values = values or {}

    async def current(self):
        return validate_contract(ConfigSnapshot(1, self.values))


def _ics(*events: str) -> bytes:
    return (
        "\r\n".join(
            (
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Tests//FF14 Calendar//EN",
                *events,
                "END:VCALENDAR",
                "",
            )
        )
    ).encode("utf-8")


def _event(uid: str, *properties: str) -> str:
    return "\r\n".join(("BEGIN:VEVENT", f"UID:{uid}", *properties, "END:VEVENT"))


class _FakeHttp:
    def __init__(self, responses: list[bytes | SourceHttpError | HttpResponse]) -> None:
        self.responses = deque(responses)
        self.requests: list[HttpRequest] = []

    async def fetch(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self.responses.popleft()
        if isinstance(response, SourceHttpError):
            raise response
        if isinstance(response, HttpResponse):
            return response
        return validate_contract(
            HttpResponse(200, {"Content-Type": "text/calendar"}, response)
        )


class _Bound:
    def __init__(self, http: _FakeHttp) -> None:
        self.http = http


class _Scopes:
    def __init__(self, http: _FakeHttp) -> None:
        self.bound = _Bound(http)
        self.invocations = []

    async def bind(self, invocation):
        self.invocations.append(invocation)
        return self.bound


class _Operations:
    supported_notification_modes = frozenset(("instant", "digest"))

    def __init__(self) -> None:
        self.views: tuple[SubscriptionView, ...] = ()
        self.created: list[SubscriptionRequest] = []
        self.revised: list[SubscriptionRequest] = []
        self.cancelled: list[tuple[str, int]] = []
        self.list_calls = 0

    async def create_request(self, invocation, request):
        del invocation
        self._validate_request(request)
        self.created.append(request)
        view = _view("sub-calendar-1", 1, dict(request.filters))
        self.views = (view,)
        return view

    async def revise_request(self, invocation, request):
        del invocation
        self._validate_request(request)
        self.revised.append(request)
        view = _view(
            request.subscription_id or "sub-calendar-1",
            (request.expected_revision or 0) + 1,
            dict(request.filters),
        )
        self.views = (view,)
        return view

    async def list_current(self, invocation):
        del invocation
        self.list_calls += 1
        return self.views

    async def cancel(self, invocation, subscription_id, *, expected_revision):
        del invocation
        self.cancelled.append((subscription_id, expected_revision))
        self.views = ()

    def _validate_request(self, request: SubscriptionRequest) -> None:
        if request.notification_mode not in self.supported_notification_modes:
            raise ValueError("notification mode is not supported")
        if (
            request.notification_mode == "instant"
            and request.digest_schedule is not None
        ):
            raise ValueError("instant request cannot carry a digest schedule")
        if request.notification_mode == "digest" and request.digest_schedule is None:
            raise ValueError("digest request requires a schedule")


def _view(subscription_id: str, revision: int, filters: dict) -> SubscriptionView:
    return validate_contract(
        SubscriptionView(
            subscription_id,
            revision,
            "owner-private-id",
            None,
            "private-route-id",
            filters,
        )
    )


def _invocation(
    *,
    origin: InvocationOrigin = InvocationOrigin.COMMAND,
) -> InvocationView:
    return validate_contract(
        InvocationView(
            invocation_id="inv-calendar-1",
            origin=origin,
            actor_id="trusted-actor",
            conversation_id="trusted-conversation",
            module_id="ff14/ff14",
            module_epoch=1,
            registry_revision=1,
            adapter_id="test-adapter",
            capability_id="calendar.subscription_manage",
        )
    )


class CalendarCollectorAndQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_query_handler_reads_each_new_default_and_explicit_values_win(
        self,
    ):
        raw = _ics(
            _event(
                "day-two",
                "SUMMARY:Second day",
                "DTSTART:20261001T120000Z",
                "DTEND:20261001T130000Z",
            )
        )
        config = _ConfigView(
            {"ff14_calendar_default_days": 1, "ff14_calendar_default_timezone": "UTC"}
        )
        http = _FakeHttp([raw, raw, raw])
        services = type("Services", (), {"config": config, "scopes": _Scopes(http)})()
        handler = CalendarQuery(
            services, clock=lambda: datetime(2026, 9, 30, 12, tzinfo=UTC)
        )
        first = await handler.invoke(_invocation(), {"region": "cn"})
        self.assertNotIn("Second day", repr(first.document.ordered_blocks))
        config.values = {
            "ff14_calendar_default_days": 3,
            "ff14_calendar_default_timezone": "Asia/Tokyo",
        }
        second = await handler.invoke(_invocation(), {"region": "cn"})
        self.assertIn("Second day", repr(second.document.ordered_blocks))
        self.assertEqual(second.document.timestamps[0].timezone_name, "Asia/Tokyo")
        explicit = await handler.invoke(
            _invocation(), {"region": "cn", "days": 1, "timezone": "UTC"}
        )
        self.assertNotIn("Second day", repr(explicit.document.ordered_blocks))
        self.assertEqual(explicit.document.timestamps[0].timezone_name, "UTC")
        missing = await handler.invoke(_invocation(), {})
        self.assertIs(missing.error.code, ErrorCode.PARAMETER_ERROR)

    async def _render(self, document):
        renderer = GenericDisplayRenderer(
            RenderingBounds(8000, 100, 10, 20, 32, 128, 8, 64, 32)
        )
        return (
            await renderer.render(
                document,
                limits=validate_contract(
                    DisplayLimits(max_pages=2, max_image_bytes=16)
                ),
            )
        ).text

    async def test_shared_reviewed_sources_keep_exact_region_identity_and_unknown_disabled(
        self,
    ):
        for region in ("cn", "global"):
            for selected in (SCHEDULE_SOURCE_ID, FALLBACK_SOURCE_ID):
                with self.subTest(region=region, selected=selected):
                    http = _FakeHttp(
                        [_ics()]
                        if selected == SCHEDULE_SOURCE_ID
                        else [SourceHttpError("upstream_error"), _ics()]
                    )
                    snapshot = await CalendarSourceReader(http).read(
                        region,
                        window_start=datetime(2026, 9, 30, tzinfo=UTC),
                        window_end=datetime(2026, 10, 7, tzinfo=UTC),
                        display_timezone="UTC",
                        reject_floating=True,
                    )
                    spec = calendar_source_spec(region, selected)
                    self.assertEqual(
                        (
                            snapshot.source_id,
                            snapshot.source_variant,
                            snapshot.source_url,
                        ),
                        (spec.source_id, spec.variant, spec.url),
                    )
                    self.assertEqual(http.requests[-1].path, spec.path)
        http = _FakeHttp([_ics()])
        services = type(
            "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
        )()
        with mock.patch(
            "modules.ff14.features.calendar.calendar_source_spec",
            side_effect=lambda region, source: None
            if source == SCHEDULE_SOURCE_ID
            else calendar_source_spec(region, source),
        ):
            result = await CalendarQuery(
                services, clock=lambda: datetime(2026, 9, 30, tzinfo=UTC)
            ).invoke(_invocation(), {"region": "cn"})
        text = await self._render(result.document)
        self.assertEqual(
            [request.source_id for request in http.requests], [FALLBACK_SOURCE_ID]
        )
        self.assertIn("主日历源未获资格，未启用。", text)
        self.assertNotIn("主日历源未能完整解析", text)
        self.assertIsNone(calendar_source_spec("cn", "unknown"))

    async def test_complete_empty_fallback_wins_over_partial_primary_events(self):
        partial = _ics(
            _event("safe", "SUMMARY:Primary-only", "DTSTART:20261001T120000Z"),
            _event(
                "unsupported", "DTSTART:20261001T120000Z", "RRULE:FREQ=YEARLY;COUNT=2"
            ),
        )
        http = _FakeHttp([partial, _ics()])
        services = type(
            "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
        )()
        result = await CalendarQuery(
            services, clock=lambda: datetime(2026, 9, 30, tzinfo=UTC)
        ).invoke(_invocation(), {"region": "cn"})
        text = await self._render(result.document)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertIn("已使用合格备用日历来源。", text)
        self.assertIn("主日历源未能完整解析，已尝试备用源。", text)
        self.assertIn("该公开来源在当前窗口未返回活动", text)
        self.assertNotIn("Primary-only", text)
        self.assertNotIn("主日历源暂不可用", text)
        self.assertLess(text.index("获取时间"), text.index("主日历源未能完整解析"))

    async def test_partial_empty_query_remains_partial_and_total_failure_is_error(self):
        partial = _ics(
            _event(
                "unsupported", "DTSTART:20261001T120000Z", "RRULE:FREQ=YEARLY;COUNT=2"
            )
        )
        for responses, status in (
            ([partial, SourceHttpError("timeout")], ResultStatus.PARTIAL_SUCCESS),
            (
                [SourceHttpError("timeout"), SourceHttpError("upstream_error")],
                ResultStatus.ERROR,
            ),
        ):
            with self.subTest(status=status):
                http = _FakeHttp(responses)
                services = type(
                    "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
                )()
                result = await CalendarQuery(
                    services, clock=lambda: datetime(2026, 9, 30, tzinfo=UTC)
                ).invoke(_invocation(), {"region": "cn"})
                self.assertIs(result.status, status)
                if status is ResultStatus.ERROR:
                    self.assertIsNone(result.document)
                    continue
                text = await self._render(result.document)
                self.assertIn("来源未完整解析，无法确认当前窗口是否无活动", text)
                self.assertIn("自动摘要暂不可用", text)
                self.assertIn("备用日历源暂不可用", text)
                self.assertIn("来源更新时间未知", text)
                self.assertIn("不保证覆盖全部活动", text)
                self.assertNotIn("当前窗口未返回活动", text)
                self.assertNotIn("暂无活动", text)

    async def test_partial_with_events_precedes_partial_empty_primary(self):
        empty = _ics(
            _event(
                "unsupported", "DTSTART:20261001T120000Z", "RRULE:FREQ=YEARLY;COUNT=2"
            )
        )
        with_events = _ics(
            _event("covered", "SUMMARY:Fallback-covered", "DTSTART:20261001T120000Z"),
            _event(
                "unsupported", "DTSTART:20261001T120000Z", "RRULE:FREQ=YEARLY;COUNT=2"
            ),
        )
        http = _FakeHttp([empty, with_events])
        services = type(
            "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
        )()
        result = await CalendarQuery(
            services, clock=lambda: datetime(2026, 9, 30, tzinfo=UTC)
        ).invoke(_invocation(), {"region": "cn"})
        text = await self._render(result.document)
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertIn("Fallback-covered", text)
        self.assertIn("已使用合格备用日历来源", text)
        self.assertLess(text.index("自动摘要暂不可用"), text.index("Fallback-covered"))

    async def test_host_defaults_change_query_window_and_explicit_parameters_win(self):
        raw = _ics(
            _event(
                "day-two",
                "SUMMARY:Second day",
                "DTSTART:20261001T120000Z",
                "DTEND:20261001T130000Z",
            )
        )
        config = FF14ConfigSnapshot(
            calendar_default_days=1, calendar_default_timezone="UTC"
        )
        for parameters, expected_zone, visible in (
            ({"region": "cn"}, "UTC", False),
            ({"region": "cn", "days": 3, "timezone": "Asia/Tokyo"}, "Asia/Tokyo", True),
        ):
            with self.subTest(parameters=parameters):
                http = _FakeHttp([raw])
                services = type(
                    "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
                )()
                result = await CalendarQuery(
                    services,
                    config=config,
                    clock=lambda: datetime(2026, 9, 30, 12, tzinfo=UTC),
                ).invoke(_invocation(), parameters)
                self.assertIs(result.status, ResultStatus.SUCCESS)
                self.assertEqual(
                    result.document.timestamps[0].timezone_name, expected_zone
                )
                tables = [
                    block
                    for block in result.document.ordered_blocks
                    if isinstance(block, TableBlock)
                ]
                self.assertEqual("Second day" in repr(tables), visible)

    def _collector(self, http: _FakeHttp, now: datetime) -> CalendarCollector:
        services = type(
            "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
        )()
        return CalendarCollector(services, clock=lambda: now)

    def _collection_view(self, parameters: NormalizedInput) -> CollectionView:
        key = validate_contract(
            CollectionKey(
                "ff14/ff14",
                COLLECTOR_ID,
                KEY_VERSION,
                SCHEDULE_SOURCE_ID,
                parameters,
                OwnerScope.public(),
            )
        )
        return validate_contract(
            CollectionView(
                key, invocation=_invocation(origin=InvocationOrigin.SCHEDULER)
            )
        )

    async def test_collector_uses_public_region_key_and_exact_utc_coverage(
        self,
    ) -> None:
        raw = _ics(
            _event(
                "utc-event",
                "SUMMARY:UTC event",
                "DTSTART:20260929T120000Z",
                "DTEND:20260929T130000Z",
            ),
            _event(
                "date-event",
                "SUMMARY:All day",
                "DTSTART;VALUE=DATE:20260930",
                "DTEND;VALUE=DATE:20261001",
            ),
        )
        http = _FakeHttp([raw])
        parameters = validate_contract(NormalizedInput({"region": "cn"}))
        now = datetime(2026, 9, 29, 12, tzinfo=UTC)
        observation = await self._collector(http, now).collect(
            self._collection_view(parameters), parameters, None
        )

        self.assertIs(observation.completeness, ObservationCompleteness.COMPLETE)
        self.assertEqual(observation.data_version, DATA_VERSION)
        self.assertIsNone(observation.source_observed_at)
        self.assertEqual(observation.collected_at, now)
        self.assertEqual(observation.key.scope, OwnerScope.public())
        self.assertEqual(dict(observation.key.parameters.values), {"region": "cn"})
        self.assertEqual(
            observation.payload["window_start"], "2026-09-28T00:00:00+00:00"
        )
        self.assertEqual(observation.payload["window_end"], "2026-10-08T00:00:00+00:00")
        self.assertEqual(observation.payload["source_id"], SCHEDULE_SOURCE_ID)
        self.assertEqual(observation.payload["source_variant"], "cn_google")
        self.assertEqual(
            observation.payload["source_version"], hashlib.sha256(raw).hexdigest()
        )
        self.assertEqual(http.requests[0].source_id, SCHEDULE_SOURCE_ID)
        self.assertIn("@import.calendar.google.com", http.requests[0].path)
        self.assertEqual(observation.covered_ids, ())
        self.assertNotIn("owner", observation.payload)
        self.assertNotIn("recipient", observation.payload)

    async def test_fallback_is_used_as_a_distinct_source_without_merging(self) -> None:
        primary = SourceHttpError("upstream_error", status_code=503)
        fallback = _ics(
            _event(
                "fallback-event",
                "SUMMARY:Fallback",
                "DTSTART:20260930T120000Z",
                "DTEND:20260930T130000Z",
            )
        )
        http = _FakeHttp([primary, fallback])
        parameters = validate_contract(NormalizedInput({"region": "global"}))
        observation = await self._collector(
            http, datetime(2026, 9, 29, 12, tzinfo=UTC)
        ).collect(self._collection_view(parameters), parameters, None)

        self.assertIs(observation.completeness, ObservationCompleteness.COMPLETE)
        self.assertEqual(observation.payload["region"], "global")
        self.assertEqual(observation.payload["source_id"], FALLBACK_SOURCE_ID)
        self.assertEqual(observation.payload["source_variant"], "global_icloud")
        self.assertEqual(len(observation.payload["occurrences"]), 1)
        self.assertEqual(
            [request.source_id for request in http.requests],
            [SCHEDULE_SOURCE_ID, FALLBACK_SOURCE_ID],
        )
        self.assertTrue(
            str(observation.payload["source_url"]).startswith(
                "https://p66-caldav.icloud.com/"
            )
        )

    async def test_partial_requires_exact_covered_occurrences_and_empty_is_failed(
        self,
    ) -> None:
        partial = _ics(
            _event(
                "safe-utc",
                "SUMMARY:Covered",
                "DTSTART:20260930T120000Z",
                "DTEND:20260930T130000Z",
            ),
            _event(
                "unsafe-floating",
                "SUMMARY:Unknown timezone",
                "DTSTART:20260930T120000",
                "DTEND:20260930T130000",
            ),
        )
        http = _FakeHttp([partial, SourceHttpError("upstream_error", status_code=503)])
        parameters = validate_contract(NormalizedInput({"region": "cn"}))
        collector = self._collector(http, datetime(2026, 9, 29, 12, tzinfo=UTC))
        observation = await collector.collect(
            self._collection_view(parameters), parameters, None
        )
        self.assertIs(observation.completeness, ObservationCompleteness.PARTIAL)
        self.assertEqual(len(observation.covered_ids), 1)
        self.assertNotEqual(observation.covered_ids[0], "calendar-partial")
        self.assertEqual(len(observation.payload["occurrences"]), 1)
        self.assertEqual(observation.payload["completeness"], "partial")

        unsafe = _ics(
            _event(
                "only-floating",
                "SUMMARY:No safe coverage",
                "DTSTART:20260930T120000",
                "DTEND:20260930T130000",
            )
        )
        empty = await self._collector(
            _FakeHttp([unsafe, unsafe]), datetime(2026, 9, 29, 12, tzinfo=UTC)
        ).collect(self._collection_view(parameters), parameters, None)
        self.assertIs(empty.completeness, ObservationCompleteness.FAILED)
        self.assertEqual(empty.covered_ids, ())

    async def test_query_defaults_and_source_fallback_provenance_are_visible(
        self,
    ) -> None:
        raw = _ics(
            _event(
                "floating",
                "SUMMARY:Floating event",
                "DTSTART:20260930T120000",
                "DTEND:20260930T130000",
            )
        )
        http = _FakeHttp([SourceHttpError("upstream_error", status_code=503), raw])
        services = type(
            "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
        )()
        query = CalendarQuery(
            services,
            clock=lambda: datetime(2026, 9, 29, 12, tzinfo=UTC),
        )
        result = await query.invoke(_invocation(), {"region": "国际服"})
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertIs(result.privacy, Privacy.PUBLIC)
        self.assertEqual(
            [request.source_id for request in http.requests],
            [SCHEDULE_SOURCE_ID, FALLBACK_SOURCE_ID],
        )
        self.assertEqual(result.document.timestamps[0].timezone_name, DEFAULT_TIMEZONE)
        blocks = result.document.ordered_blocks
        self.assertTrue(any(isinstance(block, TableBlock) for block in blocks))
        self.assertTrue(any(isinstance(block, LinksBlock) for block in blocks))
        text = " ".join(block.text for block in blocks if hasattr(block, "text"))
        self.assertIn(DEFAULT_TIMEZONE, text)
        self.assertIn("发布者时区", text)
        self.assertEqual(len(result.warnings), 1)
        rendered = await self._render(result.document)
        self.assertIn("主日历源暂不可用，已尝试备用源。", rendered)
        self.assertIn("已使用合格备用日历来源。", rendered)
        self.assertLess(rendered.index("获取时间"), rendered.index("主日历源暂不可用"))
        self.assertLess(
            rendered.index("主日历源暂不可用"), rendered.index("Floating event")
        )

    async def test_query_rejects_invalid_region_without_network(self) -> None:
        http = _FakeHttp([])
        services = type(
            "Services", (), {"config": _ConfigView(), "scopes": _Scopes(http)}
        )()
        result = await CalendarQuery(services).invoke(
            _invocation(), {"region": "other"}
        )
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertIs(result.error.code, ErrorCode.PARAMETER_ERROR)
        self.assertEqual(http.requests, [])

    def test_region_aliases_normalize_to_canonical_key_values(self) -> None:
        self.assertEqual(normalize_region("国服"), "cn")
        self.assertEqual(normalize_region("国际服"), "global")
        self.assertEqual(normalize_region("GLOBAL"), "global")


class CalendarSubscriptionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_create_handler_reads_each_default_without_rewriting_existing(
        self,
    ):
        config = _ConfigView(
            {
                "ff14_calendar_default_timezone": "UTC",
                "ff14_calendar_default_delivery_time": "13:25",
            }
        )
        operations = _Operations()
        services = type(
            "Services", (), {"config": config, "subscriptions": operations}
        )()
        handler = CalendarSubscriptionHandler(services, "subscribe")
        await handler.invoke(_invocation(), {"region": "cn"})
        config.values = {
            "ff14_calendar_default_timezone": "Asia/Tokyo",
            "ff14_calendar_default_delivery_time": "18:00",
        }
        await handler.invoke(_invocation(), {"region": "cn"})
        await handler.invoke(
            _invocation(), {"region": "global", "timezone": "UTC", "time": "09:15"}
        )
        self.assertEqual(
            [(v.filters["timezone"], v.filters["time"]) for v in operations.created],
            [("UTC", "13:25"), ("Asia/Tokyo", "18:00"), ("UTC", "09:15")],
        )
        missing = await handler.invoke(_invocation(), {})
        self.assertIs(missing.error.code, ErrorCode.PARAMETER_ERROR)

    async def test_new_subscriptions_take_host_defaults_and_explicit_values_win(self):
        config = FF14ConfigSnapshot(
            calendar_default_timezone="UTC", calendar_default_delivery_time="13:25"
        )
        for parameters, zone, local_time in (
            ({"region": "cn"}, "UTC", "13:25"),
            (
                {"region": "cn", "timezone": "Asia/Tokyo", "time": "09:15"},
                "Asia/Tokyo",
                "09:15",
            ),
        ):
            operations = _Operations()
            services = type(
                "Services", (), {"config": _ConfigView(), "subscriptions": operations}
            )()
            result = await CalendarSubscriptionHandler(
                services, "subscribe", config=config
            ).invoke(_invocation(), parameters)
            self.assertIs(result.status, ResultStatus.SUCCESS)
            self.assertEqual(operations.created[0].filters["timezone"], zone)
            self.assertEqual(operations.created[0].filters["time"], local_time)

    async def test_reloaded_defaults_never_rewrite_existing_subscription_on_update(
        self,
    ):
        operations = _Operations()
        filters = {
            "kind": FILTER_KIND,
            "region": "cn",
            "timezone": "Asia/Tokyo",
            "time": "09:15",
        }
        operations.views = (_view("existing", 2, filters),)
        services = type(
            "Services", (), {"config": _ConfigView(), "subscriptions": operations}
        )()
        config = FF14ConfigSnapshot(
            calendar_default_timezone="UTC", calendar_default_delivery_time="13:25"
        )
        listed = await CalendarSubscriptionHandler(
            services, "list", config=config
        ).invoke(_invocation(), {})
        self.assertIs(listed.status, ResultStatus.SUCCESS)
        self.assertEqual(dict(operations.views[0].filters), filters)
        result = await CalendarSubscriptionHandler(
            services, "update", config=config
        ).invoke(
            _invocation(),
            {"subscription_id": "existing", "expected_revision": 2, "time": "10:00"},
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertEqual(operations.revised[0].filters["timezone"], "Asia/Tokyo")
        self.assertEqual(operations.revised[0].filters["time"], "10:00")

    def _handler(
        self, operations: _Operations, action: str
    ) -> CalendarSubscriptionHandler:
        services = type(
            "Services", (), {"config": _ConfigView(), "subscriptions": operations}
        )()
        return CalendarSubscriptionHandler(services, action)

    async def test_create_is_explicit_and_public_parameters_exclude_user_data(
        self,
    ) -> None:
        operations = _Operations()
        result = await self._handler(operations, "subscribe").invoke(
            _invocation(), {"region": "国服"}
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertIs(result.privacy, Privacy.PRIVATE)
        request = operations.created[0]
        self.assertEqual(request.type_id, SUMMARY_TYPE_ID)
        self.assertEqual(dict(request.collector_parameters), {"region": "cn"})
        self.assertEqual(
            dict(request.filters),
            {
                "kind": FILTER_KIND,
                "region": "cn",
                "timezone": DEFAULT_TIMEZONE,
                "time": DEFAULT_LOCAL_TIME,
            },
        )
        self.assertIn(
            request.notification_mode, operations.supported_notification_modes
        )
        self.assertEqual(request.notification_mode, "instant")
        self.assertIsNone(request.digest_schedule)
        self.assertNotIn("owner_id", request.collector_parameters)

    async def test_list_shows_only_calendar_rows_and_never_route_or_owner(self) -> None:
        operations = _Operations()
        operations.views = (
            _view(
                "calendar-row",
                2,
                {
                    "kind": FILTER_KIND,
                    "region": "global",
                    "timezone": "Asia/Tokyo",
                    "time": "09:15",
                },
            ),
            _view("other-row", 1, {"kind": "other", "region": "cn"}),
        )
        result = await self._handler(operations, "list").invoke(_invocation(), {})
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertIs(result.privacy, Privacy.PRIVATE)
        text = " ".join(block.text for block in result.document.ordered_blocks)
        self.assertIn("calendar-row", text)
        self.assertIn("Asia/Tokyo", text)
        self.assertNotIn("other-row", text)
        self.assertNotIn("owner-private-id", text)
        self.assertNotIn("private-route-id", text)

    async def test_list_pages_are_bounded_and_expose_every_row(self) -> None:
        operations = _Operations()
        operations.views = tuple(
            _view(
                f"calendar-row-{index}",
                1,
                {
                    "kind": FILTER_KIND,
                    "region": "cn",
                    "timezone": DEFAULT_TIMEZONE,
                    "time": DEFAULT_LOCAL_TIME,
                },
            )
            for index in range(1, 22)
        )
        page_one = await self._handler(operations, "list").invoke(
            _invocation(), {"page": 1}
        )
        page_two = await self._handler(operations, "list").invoke(
            _invocation(), {"page": 2}
        )
        page_three = await self._handler(operations, "list").invoke(
            _invocation(), {"page": 3}
        )
        text_one = " ".join(block.text for block in page_one.document.ordered_blocks)
        text_two = " ".join(block.text for block in page_two.document.ordered_blocks)
        text_three = " ".join(
            block.text for block in page_three.document.ordered_blocks
        )
        self.assertIn("calendar-row-1", text_one)
        self.assertNotIn("calendar-row-11", text_one)
        self.assertIn("calendar-row-11", text_two)
        self.assertIn("calendar-row-21", text_three)
        self.assertIn("21", text_three)

    async def test_update_and_cancel_use_exact_revisions_and_preserve_region(
        self,
    ) -> None:
        operations = _Operations()
        operations.views = (
            _view(
                "calendar-row",
                4,
                {
                    "kind": FILTER_KIND,
                    "region": "cn",
                    "timezone": DEFAULT_TIMEZONE,
                    "time": DEFAULT_LOCAL_TIME,
                },
            ),
        )
        handler = self._handler(operations, "update")
        result = await handler.invoke(
            _invocation(),
            {
                "subscription_id": "calendar-row",
                "expected_revision": 4,
                "time": "09:30",
            },
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        updated = operations.revised[0]
        self.assertEqual(dict(updated.collector_parameters), {"region": "cn"})
        self.assertEqual(updated.expected_revision, 4)
        self.assertEqual(updated.subscription_id, "calendar-row")
        self.assertEqual(updated.filters["time"], "09:30")

        cancelled = await self._handler(operations, "cancel").invoke(
            _invocation(),
            {"subscription_id": "calendar-row", "expected_revision": 5},
        )
        self.assertIs(cancelled.status, ResultStatus.SUCCESS)
        self.assertEqual(operations.cancelled, [("calendar-row", 5)])

    async def test_command_without_conversation_kind_and_stale_revision(self) -> None:
        operations = _Operations()
        operations.views = (
            _view(
                "calendar-row",
                2,
                {
                    "kind": FILTER_KIND,
                    "region": "cn",
                    "timezone": DEFAULT_TIMEZONE,
                    "time": DEFAULT_LOCAL_TIME,
                },
            ),
        )
        listed = await self._handler(operations, "list").invoke(_invocation(), {})
        self.assertIs(listed.status, ResultStatus.SUCCESS)
        self.assertEqual(operations.list_calls, 1)

        stale = await self._handler(operations, "cancel").invoke(
            _invocation(),
            {"subscription_id": "calendar-row", "expected_revision": 1},
        )
        self.assertIs(stale.status, ResultStatus.ERROR)
        self.assertEqual(operations.cancelled, [])

    async def test_non_command_context_is_rejected_before_operations(self) -> None:
        operations = _Operations()
        result = await self._handler(operations, "list").invoke(
            _invocation(origin=InvocationOrigin.SCHEDULER), {}
        )
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertIs(result.error.code, ErrorCode.AUTH_REQUIRED)
        self.assertEqual(operations.list_calls, 0)


if __name__ == "__main__":
    unittest.main()
