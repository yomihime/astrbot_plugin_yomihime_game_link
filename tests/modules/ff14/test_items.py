from __future__ import annotations

import json
import unittest
from collections import deque
from pathlib import Path
from typing import Any

from ygl_test_subject.api.results import ErrorCode, ResultStatus
from ygl_test_subject.api.services import HttpRequest, HttpResponse
from ygl_test_subject.modules.ff14.features.item_sources import (
    GARLAND_SOURCE,
    XIVAPI_SOURCE,
    parse_garland_acquisition,
    parse_item_search_page,
    parse_xivapi_item_detail,
)
from ygl_test_subject.modules.ff14.features.items import ItemLookup
from ygl_test_subject.modules.ff14.models import AcquisitionKind

from yomihime_sdk.api.services import SourceHttpError

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "ff14" / "items.json"


class _Scope:
    def __init__(self, http: "_FakeHttp") -> None:
        self.http = http


class _Scopes:
    def __init__(self, http: "_FakeHttp") -> None:
        self._scope = _Scope(http)

    async def bind(self, invocation):
        del invocation
        return self._scope


class _FakeHttp:
    def __init__(
        self, responses: list[dict[str, Any] | bytes | SourceHttpError]
    ) -> None:
        self.responses = deque(responses)
        self.requests: list[HttpRequest] = []

    async def fetch(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self.responses.popleft()
        if isinstance(response, SourceHttpError):
            raise response
        body = (
            response if isinstance(response, bytes) else json.dumps(response).encode()
        )
        return HttpResponse(200, {"Content-Type": "application/json"}, body)


class ItemLookupTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _handler(self, http: _FakeHttp) -> ItemLookup:
        services = type("Services", (), {"scopes": _Scopes(http)})()
        return ItemLookup(services)

    async def test_ambiguous_search_uses_bounded_cursor_and_never_selects_first(
        self,
    ) -> None:
        first = self.fixture["xivcdn"]["search_first_page"]
        second = self.fixture["xivcdn"]["search_second_page"]
        # Include a literal percent sign to assert that the module keeps a raw,
        # structured query value and leaves URI encoding to the Core transport.
        http = _FakeHttp([first, second])
        result = await self._handler(http).invoke(None, {"query": "Copper 50% Ore"})

        self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
        self.assertEqual(len(http.requests), 2)
        first_query = dict(http.requests[0].query)
        second_query = dict(http.requests[1].query)
        self.assertEqual(first_query["query"], 'Name~"Copper 50% Ore"')
        self.assertNotIn("cursor", first_query)
        self.assertEqual(second_query["cursor"], "synthetic-opaque-cursor-page-2")
        self.assertEqual(http.requests[0].source_id, XIVAPI_SOURCE)
        self.assertEqual(http.requests[0].path, "/api/search")
        self.assertIn("Sample Ore Alpha", result.document.ordered_blocks[0].text)
        self.assertIn("ID 90003", result.document.ordered_blocks[2].text)

    async def test_page_cap_is_visible_and_single_partial_page_is_not_auto_selected(
        self,
    ) -> None:
        first = {
            "results": [{"row_id": 90001, "fields": {"Name": "Synthetic Ore"}}],
            "next": "opaque-2",
        }
        second = {
            "results": [{"row_id": 90002, "fields": {"Name": "Synthetic Ore II"}}],
            "next": "opaque-3",
        }
        http = _FakeHttp([first, second])
        result = await self._handler(http).invoke(None, {"query": "Synthetic Ore"})
        self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
        self.assertIn(
            "结果未穷尽",
            " ".join(block.text for block in result.document.ordered_blocks),
        )
        self.assertEqual(len(http.requests), 2)

    async def test_numeric_id_skips_search_and_returns_partial_when_garland_is_unavailable(
        self,
    ) -> None:
        primary = self.fixture["xivcdn"]["item_detail"]
        http = _FakeHttp([primary, SourceHttpError("upstream_error", status_code=429)])
        result = await self._handler(http).invoke(None, {"query": "90001"})

        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(
            [(request.source_id, request.path) for request in http.requests],
            [
                (XIVAPI_SOURCE, "/api/sheet/Item/90001"),
                (GARLAND_SOURCE, "/db/doc/item/chs/3/90001.json"),
            ],
        )
        text = " ".join(
            block.text
            for block in result.document.ordered_blocks
            if hasattr(block, "text")
        )
        self.assertIn("Sample Ore Alpha", result.document.title)
        self.assertIn("物品等级：7", text)
        self.assertTrue(
            any(warning.startswith("Garland") for warning in result.warnings)
        )
        self.assertEqual(len(result.document.ordered_blocks[-1].links), 2)

    async def test_unavailable_garland_keeps_resolved_xivapi_and_routes(self) -> None:
        http = _FakeHttp(
            [
                self.fixture["xivcdn"]["item_detail"],
                self.fixture["garland"]["linked_item_detail"],
            ]
        )
        result = await self._handler(http).invoke(None, {"query": "90001"})
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertIn(
            "来源详情未关联",
            " ".join(
                block.text
                for block in result.document.ordered_blocks
                if hasattr(block, "text")
            ),
        )
        self.assertTrue(
            any(warning.startswith("Garland") for warning in result.warnings)
        )

    async def test_malformed_garland_ids_keep_xivapi_details_as_partial_success(
        self,
    ) -> None:
        invalid_cost_id = {
            "item": {
                "id": 90001,
                "craft": [{"ingredients": [{"id": 0, "amount": 1}]}],
            },
            "partials": [],
        }
        overlong_route_id = {
            "item": {"id": 90001, "nodes": ["9" * 5_000]},
            "partials": [],
        }
        oversized_json_integer = (
            '{"item":{"id":90001,"nodes":[' + "9" * 5_000 + ']},"partials":[]}'
        ).encode("ascii")
        for case, malformed in (
            ("non-positive cost ID", invalid_cost_id),
            ("overlong route ID", overlong_route_id),
            ("oversized JSON integer", oversized_json_integer),
        ):
            with self.subTest(case=case):
                http = _FakeHttp([self.fixture["xivcdn"]["item_detail"], malformed])
                result = await self._handler(http).invoke(None, {"query": "90001"})
                self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
                self.assertEqual(result.document.title, "Sample Ore Alpha")
                self.assertTrue(
                    any(warning.startswith("Garland") for warning in result.warnings)
                )
                self.assertEqual(len(http.requests), 2)

    async def test_search_rate_limit_is_a_stable_error(self) -> None:
        http = _FakeHttp([SourceHttpError("upstream_error", status_code=429)])
        result = await self._handler(http).invoke(None, {"query": "Ore"})
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertIs(result.error.code, ErrorCode.RATE_LIMITED)

    async def test_item_404_is_not_found_but_search_404_is_an_upstream_error(
        self,
    ) -> None:
        item_http = _FakeHttp([SourceHttpError("upstream_error", status_code=404)])
        item_result = await self._handler(item_http).invoke(None, {"query": "90001"})
        self.assertIs(item_result.error.code, ErrorCode.NOT_FOUND)

        search_http = _FakeHttp([SourceHttpError("upstream_error", status_code=404)])
        search_result = await self._handler(search_http).invoke(
            None, {"query": "Synthetic Ore"}
        )
        self.assertIs(search_result.error.code, ErrorCode.UPSTREAM_ERROR)

    def test_source_parsers_preserve_nested_and_typed_reference_meaning(self) -> None:
        page = parse_item_search_page(self.fixture["xivcdn"]["search_first_page"])
        self.assertEqual(page.candidates[0].item_id, 90001)
        detail = parse_xivapi_item_detail(
            self.fixture["xivcdn"]["item_detail"], requested_id=90001
        )
        self.assertEqual(detail.item_level, 7)
        routes, warnings = parse_garland_acquisition(
            self.fixture["garland"]["linked_item_detail"], requested_id=90001
        )
        self.assertGreaterEqual(len(routes), 9)
        self.assertTrue(any(route.kind is AcquisitionKind.CRAFT for route in routes))
        self.assertTrue(any(route.kind is AcquisitionKind.QUEST for route in routes))
        self.assertTrue(warnings)

    async def test_unsupported_query_syntax_is_rejected_before_http(self) -> None:
        http = _FakeHttp([])
        result = await self._handler(http).invoke(None, {"query": 'Name" OR 1'})
        self.assertIs(result.error.code, ErrorCode.PARAMETER_ERROR)
        self.assertEqual(http.requests, [])


if __name__ == "__main__":
    unittest.main()
