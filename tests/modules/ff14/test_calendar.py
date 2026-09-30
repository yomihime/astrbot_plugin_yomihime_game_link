from __future__ import annotations

import hashlib
import unittest
from collections import deque
from datetime import UTC, datetime

from modules.ff14.features.calendar import (
    COLLECTOR_ID,
    DATA_VERSION,
    FALLBACK_SOURCE_ID,
    KEY_VERSION,
    SCHEDULE_SOURCE_ID,
    CalendarCollector,
    CalendarQuery,
    normalize_region,
)
from modules.ff14.features.calendar_evaluator import (
    DEFAULT_LOCAL_TIME,
    DEFAULT_TIMEZONE,
    FILTER_KIND,
    SUMMARY_TYPE_ID,
)
from modules.ff14.features.calendar_subscriptions import CalendarSubscriptionHandler
from yomihime_sdk.api.contexts import (
    InvocationOrigin,
    InvocationView,
)
from yomihime_sdk.api.display import LinksBlock, Privacy, TableBlock
from yomihime_sdk.api.results import ErrorCode, ResultStatus
from yomihime_sdk.api.services import HttpRequest, HttpResponse, SourceHttpError
from yomihime_sdk.api.storage import OwnerScope
from yomihime_sdk.api.subscriptions import (
    CollectionKey,
    CollectionView,
    NormalizedInput,
    ObservationCompleteness,
    SubscriptionRequest,
    SubscriptionView,
)


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
        return HttpResponse(200, {"Content-Type": "text/calendar"}, response)


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
    return SubscriptionView(
        subscription_id,
        revision,
        "owner-private-id",
        None,
        "private-route-id",
        filters,
    )


def _invocation(
    *,
    origin: InvocationOrigin = InvocationOrigin.COMMAND,
) -> InvocationView:
    return InvocationView(
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


class CalendarCollectorAndQueryTests(unittest.IsolatedAsyncioTestCase):
    def _collector(self, http: _FakeHttp, now: datetime) -> CalendarCollector:
        services = type("Services", (), {"scopes": _Scopes(http)})()
        return CalendarCollector(services, clock=lambda: now)

    def _collection_view(self, parameters: NormalizedInput) -> CollectionView:
        key = CollectionKey(
            "ff14/ff14",
            COLLECTOR_ID,
            KEY_VERSION,
            SCHEDULE_SOURCE_ID,
            parameters,
            OwnerScope.public(),
        )
        return CollectionView(
            key, invocation=_invocation(origin=InvocationOrigin.SCHEDULER)
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
        parameters = NormalizedInput({"region": "cn"})
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
        parameters = NormalizedInput({"region": "global"})
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
        parameters = NormalizedInput({"region": "cn"})
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
        services = type("Services", (), {"scopes": _Scopes(http)})()
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

    async def test_query_rejects_invalid_region_without_network(self) -> None:
        http = _FakeHttp([])
        services = type("Services", (), {"scopes": _Scopes(http)})()
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
    def _handler(
        self, operations: _Operations, action: str
    ) -> CalendarSubscriptionHandler:
        services = type("Services", (), {"subscriptions": operations})()
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
