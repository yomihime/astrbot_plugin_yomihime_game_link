"""Offline source/SDK tests; synthetic prices and directory fixtures, no network."""

from __future__ import annotations

import asyncio
import copy
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ygl_test_subject.adapters.astrbot.web_public import project_result
from ygl_test_subject.infrastructure.http import SourceHttpService
from ygl_test_subject.modules.ff14.features.market import (
    MarketClient,
    _result,
    parse_aggregated,
    parse_listings,
)
from ygl_test_subject.modules.ff14.features.market_sources import (
    DCS_PATH,
    SOURCE_ROOT,
    UNIVERSALIS_SOURCE,
    WORLDS_PATH,
    CatalogSnapshot,
    MarketDeadlineError,
    MarketPayloadError,
    MarketSession,
    MarketSourceClient,
    Provenance,
    _json_bytes,
    number,
    parse_catalog,
    source_time,
)
from ygl_test_subject.modules.ff14.query_resolution import (
    GLOBAL_REGIONS,
    CatalogEntry,
    MarketQueryResolver,
    QueryDefaults,
    QueryResolutionError,
    ScopeCatalog,
)

from yomihime_sdk.api.results import ErrorCode, ResultStatus
from yomihime_sdk.api.services import HttpRequest, HttpResponse, SourceHttpError
from yomihime_sdk.api.storage import CacheEntry

NOW = datetime(2026, 10, 5, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)
REGION_IDS = {
    "China": 90001,
    "North-America": 90002,
    "Europe": 90003,
    "Japan": 90004,
    "Oceania": 90005,
}
WORLDS = [{"id": value, "name": "Synthetic" + key} for key, value in REGION_IDS.items()]
DCS = [
    {"name": "Synthetic" + region.replace("-", ""), "region": region, "worlds": [world]}
    for region, world in REGION_IDS.items()
]


def catalog():
    return CatalogSnapshot(
        parse_catalog(copy.deepcopy(WORLDS), copy.deepcopy(DCS)),
        (
            Provenance(SOURCE_ROOT + WORLDS_PATH, NOW),
            Provenance(SOURCE_ROOT + DCS_PATH, NOW),
        ),
    )


def query(**parameters):
    return MarketQueryResolver(catalog().catalog).parse(
        {"query": "44091", **parameters}, QueryDefaults("cn", 2, 3)
    )


def aggregated(region="China", *, price=100, quality="all", level="region"):
    world = REGION_IDS[region]
    metric = {
        "minListing": {level: {"price": price, "worldId": world}},
        "recentPurchase": {
            level: {"price": price + 9, "worldId": world, "timestamp": MS - 2000}
        },
        "averageSalePrice": {level: {"price": price + 2.5}},
        "dailySaleVelocity": {level: {"quantity": 1.25}},
    }
    return {
        "results": [
            {
                "itemId": 44091,
                "nq": copy.deepcopy(metric),
                "hq": copy.deepcopy(metric),
                "worldUploadTimes": [{"worldId": world, "timestamp": MS - 5000}],
            }
        ],
        "failedItems": [],
    }


def listing(price, *, region="China", hq=False, ordinal=0):
    return {
        "pricePerUnit": price,
        "quantity": ordinal + 1,
        "hq": hq,
        "worldID": REGION_IDS[region],
        "lastReviewTime": int(NOW.timestamp()) - 2,
        "sellerID": "synthetic-private-seller",
        "retainerName": "synthetic-private-retainer",
    }


def currently(region="China", *, rows=None):
    return {
        "itemID": 44091,
        "regionName": region,
        "lastUploadTime": MS - 5000,
        "hasData": True,
        "worldUploadTimes": {str(REGION_IDS[region]): MS - 5000},
        "listings": rows if rows is not None else [listing(100, region=region)],
    }


class FakeHttp:
    def __init__(self, callback):
        self.callback, self.requests = callback, []

    async def fetch(self, request):
        self.requests.append(request)
        value = self.callback(request)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, HttpResponse):
            return value
        return HttpResponse(200, {}, json.dumps(value, allow_nan=False).encode())


class FakeCache:
    def __init__(self, wall=lambda: NOW):
        self.values, self.puts, self.wall = {}, [], wall

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, payload, *, ttl_seconds):
        self.puts.append((key, copy.deepcopy(payload), ttl_seconds))
        entry = CacheEntry(key, payload, self.wall() + timedelta(seconds=ttl_seconds))
        self.values[key] = entry
        return entry


class CatalogContractTests(unittest.TestCase):
    def test_official_no_dc_id_shape_and_china_equivalence_without_fabrication(self):
        # Minimized factual shape from the current official catalog capture;
        # this is not the complete catalog and is not a live client acceptance.
        cat = parse_catalog(
            [{"id": 1043, "name": "紫水栈桥"}],
            [{"name": "猫小胖", "region": "中国", "worlds": [1043]}],
        )
        dc, world = cat.entries
        self.assertEqual(
            (dc.id, world.dc_id, world.id, world.region),
            ("猫小胖", "猫小胖", 1043, "China"),
        )
        self.assertEqual(dc.aliases, ())
        resolver = MarketQueryResolver(cat)
        resolved = resolver.parse(
            {"query": "44091", "dc": "猫小胖"}, QueryDefaults("global", 1, 2)
        )
        self.assertEqual(resolved.scope.target, "猫小胖")
        with self.assertRaises(QueryResolutionError):
            resolver.parse({"query": "44091", "dc": 1}, QueryDefaults("cn", 1, 2))
        with self.assertRaises(QueryResolutionError):
            resolver.parse({"query": "44091", "dc": "猫区"}, QueryDefaults("cn", 1, 2))

    def test_named_identifier_is_dc_only_canonical_and_has_correct_parent(self):
        for entry in (
            ("dc", "fake", "canonical", "China"),
            ("world", "world", "world", "China", "dc"),
            ("world", True, "world", "China", "dc"),
        ):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                CatalogEntry(*entry)
        with self.assertRaises(ValueError):
            ScopeCatalog(
                (
                    CatalogEntry("dc", "dc", "dc", "China"),
                    CatalogEntry("world", 1, "w", "Europe", "dc"),
                )
            )

    def test_directory_mappings_duplicates_missing_and_bounds_are_strict(self):
        cases = [
            (None, DCS),
            ([], DCS),
            (WORLDS, None),
            (WORLDS, []),
            (WORLDS + [WORLDS[0]], DCS),
            (WORLDS, DCS + [DCS[0]]),
            (WORLDS, DCS[:-1]),
            (WORLDS, [{**DCS[0], "worlds": [90001, 90001]}] + DCS[1:]),
            (WORLDS, [{**DCS[0], "worlds": [123]}] + DCS[1:]),
            (
                [{"id": True, "name": "invalid"}],
                [{"name": "dc", "region": "China", "worlds": [True]}],
            ),
            (WORLDS, [{**DCS[0], "name": "bad/dc"}] + DCS[1:]),
            ([{"id": i + 1, "name": "x"} for i in range(1025)], DCS),
        ]
        for worlds, dcs in cases:
            with self.subTest(worlds=worlds), self.assertRaises(MarketPayloadError):
                parse_catalog(copy.deepcopy(worlds), copy.deepcopy(dcs))

    def test_extra_directory_region_is_preserved_and_unsupported(self):
        cat = parse_catalog(
            [{"id": 1, "name": "SyntheticCloud"}],
            [{"name": "NA Cloud DC (Beta)", "region": "NA-Cloud-DC", "worlds": [1]}],
        )
        self.assertEqual(cat.entries[0].id, "NA Cloud DC (Beta)")
        self.assertEqual(cat.entries[1].region, "NA-Cloud-DC")
        with self.assertRaises(QueryResolutionError) as raised:
            MarketQueryResolver(cat).parse(
                {"query": "1", "server": "SyntheticCloud"},
                QueryDefaults("global", 1, 2),
            )
        self.assertIs(raised.exception.code, ErrorCode.UNSUPPORTED)

    def test_json_limits_duplicate_keys_and_nonfinite_are_rejected(self):
        for body in (b'{"x":1,"x":2}', b'{"x":NaN}', b"not-json", b"x" * 1_048_577):
            with self.subTest(body=body[:40]), self.assertRaises(MarketPayloadError):
                _json_bytes(body)
        for value in (True, -1, float("nan"), float("inf"), 10**500):
            with self.assertRaises(MarketPayloadError):
                number(value)

    def test_timestamp_units_unknown_future_and_invalid(self):
        self.assertEqual(source_time(MS, milliseconds=True), NOW)
        self.assertEqual(source_time(int(NOW.timestamp()), milliseconds=False), NOW)
        self.assertIsNone(source_time(None, milliseconds=True))
        self.assertIsNone(source_time(0, milliseconds=True))
        self.assertGreater(source_time(MS + 1000, milliseconds=True), NOW)
        for value in (True, -1, "123", 1.5, 253_402_300_800_000):
            with self.assertRaises(MarketPayloadError):
                source_time(value, milliseconds=True)


class MarketParserTests(unittest.TestCase):
    def test_narrow_aggregate_layer_never_uses_broader_metric(self):
        payload = aggregated()
        metrics = payload["results"][0]["nq"]
        for field, data in metrics.items():
            data["world"] = {
                "price": 7,
                "quantity": 7,
                "worldId": None,
                "timestamp": MS - 1000,
            }
            data["dc"] = {
                "price": 11,
                "quantity": 11,
                "worldId": 90001,
                "timestamp": MS - 1000,
            }
        world_query = query(server=90001, quality="nq")
        parsed = parse_aggregated(payload, world_query, "China", catalog())
        self.assertEqual(
            (parsed.quotes[0].minimum, parsed.quotes[0].minimum_world), (7, 90001)
        )
        self.assertEqual(
            parsed.quotes[0].minimum_uploaded_at, NOW - timedelta(seconds=5)
        )
        del metrics["minListing"]["world"]
        parsed = parse_aggregated(payload, world_query, "China", catalog())
        self.assertIsNone(parsed.quotes[0].minimum)
        self.assertTrue(parsed.incomplete)
        dc_query = query(dc="SyntheticChina", quality="nq")
        self.assertEqual(
            parse_aggregated(payload, dc_query, "China", catalog()).quotes[0].minimum,
            11,
        )

    def test_aggregate_quality_missing_values_and_identity_are_strict(self):
        for mutate in (
            lambda p: p["results"][0].update(itemId=True),
            lambda p: p.update(failedItems=[44091]),
            lambda p: p.update(failedItems=False),
            lambda p: p["results"][0]["nq"]["minListing"]["region"].update(price=True),
            lambda p: p["results"][0]["nq"]["minListing"]["region"].update(price=1.5),
            lambda p: p["results"][0]["nq"]["minListing"]["region"].update(
                worldId=90003
            ),
            lambda p: p["results"][0]["nq"]["minListing"].update(region={}),
        ):
            payload = aggregated()
            mutate(payload)
            with self.assertRaises(MarketPayloadError):
                parse_aggregated(payload, query(quality="nq"), "China", catalog())
        payload = aggregated()
        payload["results"][0]["hq"] = None
        parsed = parse_aggregated(payload, query(), "China", catalog())
        self.assertIsNone(parsed.quotes[1].minimum)
        self.assertTrue(parsed.incomplete)

    def test_listing_time_units_scope_identity_and_quality_never_default(self):
        parsed = parse_listings(
            currently(), query(intent="listings"), "China", catalog()
        )
        self.assertEqual(parsed.listings[0].reviewed_at, NOW - timedelta(seconds=2))
        self.assertEqual(parsed.listings[0].uploaded_at, NOW - timedelta(seconds=5))
        payload = currently()
        payload["regionName"] = "中国"
        self.assertEqual(
            len(
                parse_listings(
                    payload, query(intent="listings"), "China", catalog()
                ).listings
            ),
            1,
        )
        for mutate in (
            lambda p: p.update(itemID=1),
            lambda p: p.update(regionName="Europe"),
            lambda p: p["listings"][0].pop("hq"),
            lambda p: p["listings"][0].update(hq=0),
            lambda p: p["listings"][0].update(worldID=90003),
            lambda p: p["listings"][0].update(quantity=0),
            lambda p: p.update(hasData=False),
            lambda p: p.update(listings=[listing(1)] * 7),
        ):
            payload = currently()
            mutate(payload)
            with self.assertRaises(MarketPayloadError):
                parse_listings(payload, query(intent="listings"), "China", catalog())

    def test_quality_filter_precedes_limit_and_unknown_world_is_not_filled(self):
        payload = currently(rows=[listing(i + 1, hq=i >= 3) for i in range(6)])
        parsed = parse_listings(
            payload, query(intent="listings", quality="hq"), "China", catalog()
        )
        self.assertEqual([row.price_per_unit for row in parsed.listings], [4, 5, 6])
        self.assertTrue(parsed.incomplete)  # upstream ignored the requested filter
        payload["listings"][0].pop("worldID")
        parsed = parse_listings(payload, query(intent="listings"), "China", catalog())
        self.assertIsNone(parsed.listings[0].world_id)
        self.assertTrue(parsed.incomplete)


class MarketExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_prices_keep_all_source_digits_without_six_digit_rounding(
        self,
    ):
        for intent in ("min", "overview"):
            client, _ = self.client(lambda request: aggregated(price=1_234_567))
            execution = await client.execute(query(intent=intent, quality="hq"))
            text = "\n".join(
                getattr(block, "text", "")
                for block in execution.result.document.ordered_blocks
            )
            self.assertIn("1234567 Gil/单位", text)
            self.assertEqual(execution.minimums[0][2].minimum, 1_234_567)
            if intent == "overview":
                self.assertIn("1234569.5", text)

    async def test_direct_context_unknown_names_and_times_do_not_borrow_source_metadata(
        self,
    ):
        client, http = self.client(lambda request: aggregated())
        execution = await client.execute(query(intent="min", quality="hq"))
        q = execution.query
        for query_value, snapshot in (
            (q, None),
            (replace(q, scope=replace(q.scope, regions=("Japan",))), catalog()),
            (replace(q, scope=replace(q.scope, kind="world", target=99999)), catalog()),
            (
                replace(q, scope=replace(q.scope, kind="dc", target="different")),
                catalog(),
            ),
        ):
            result = _result(
                query_value,
                execution.outcomes,
                execution.listings,
                execution.minimums,
                False,
                NOW,
                snapshot,
            )
            text = "\n".join(
                getattr(block, "text", "") for block in result.document.ordered_blocks
            )
            self.assertNotIn("SyntheticChina", text)
            self.assertIn("服务器名称未知", text)
            self.assertIn("World 90001", text)
        quality, region, quote = execution.minimums[0]
        unknown = replace(quote, minimum_uploaded_at=None)
        outcome = replace(
            execution.outcomes[0],
            provenance=Provenance(
                execution.outcomes[0].provenance.url, NOW - timedelta(minutes=20), True
            ),
        )
        result = _result(
            q, (outcome,), (), ((quality, region, unknown),), False, NOW, catalog()
        )
        row = next(
            block.text
            for block in result.document.ordered_blocks
            if hasattr(block, "text") and "Gil/单位" in block.text
        )
        self.assertIn("World 数据上传 未知", row)
        self.assertIn("1200 秒", row)
        self.assertIn("缓存原获取时间", row)
        self.assertNotIn("World 数据上传 2026", row)
        self.assertEqual(len(http.requests), 1)

    async def test_display_price_rows_keep_verified_name_and_distinct_time_semantics(
        self,
    ):
        for intent in ("min", "overview", "listings"):
            with self.subTest(intent=intent):
                client, http = self.client(
                    lambda request: currently(rows=[listing(100, hq=True)])
                    if intent == "listings"
                    else aggregated()
                )
                execution = await client.execute(query(intent=intent, quality="hq"))
                lines = [
                    getattr(block, "text", "")
                    for block in execution.result.document.ordered_blocks
                ]
                row = next(
                    line for line in lines if "Gil/单位" in line and "100" in line
                )
                self.assertIn("SyntheticChina", row)
                self.assertIn("World 90001", row)
                self.assertIn("Universalis", row)
                self.assertIn("来源获取", row)
                self.assertIn("World 数据上传", row)
                self.assertNotIn("挂牌来源时间", row)
                if intent == "listings":
                    self.assertIn("来源最近审核", row)
                    self.assertIn("最多 5 条", "\n".join(lines))
                else:
                    self.assertIn("本次返回数据中的最低挂牌", row)
                self.assertLessEqual(len(execution.result.document.ordered_blocks), 32)
                self.assertEqual(len(http.requests), 1)

    def client(self, callback, cache=None, *, clock=None, wall=lambda: NOW):
        http = FakeHttp(callback)
        session = MarketSession(
            http, cache, wall_clock=wall, **({"clock": clock} if clock else {})
        )
        return MarketClient(session, catalog()), http

    async def test_global_aggregate_four_requests_distinct_metrics_without_averaging(
        self,
    ):
        prices = {region: 100 + i for i, region in enumerate(GLOBAL_REGIONS)}
        client, http = self.client(
            lambda request: aggregated(
                request.path.split("/")[-2], price=prices[request.path.split("/")[-2]]
            )
        )
        execution = await client.execute(query(region="global"))
        self.assertIs(execution.result.status, ResultStatus.SUCCESS)
        self.assertEqual(len(http.requests), 4)
        self.assertEqual(
            [outcome.region for outcome in execution.outcomes], list(GLOBAL_REGIONS)
        )
        self.assertEqual(
            [o.data.quotes[0].average_sale_price for o in execution.outcomes],
            [102.5, 103.5, 104.5, 105.5],
        )
        self.assertTrue(all("global" not in request.path for request in http.requests))
        data = project_result(execution.result)
        self.assertLessEqual(len(data["document"]["blocks"]), 32)

    async def test_min_uses_aggregate_only_and_keeps_winning_region_world(self):
        prices = {"North-America": 200, "Europe": 80, "Japan": 90, "Oceania": 100}
        client, http = self.client(
            lambda request: aggregated(
                request.path.split("/")[-2], price=prices[request.path.split("/")[-2]]
            )
        )
        execution = await client.execute(
            query(region="global", intent="min", quality="hq")
        )
        self.assertEqual(
            (
                execution.minimums[0][0],
                execution.minimums[0][1],
                execution.minimums[0][2].minimum_world,
            ),
            ("hq", "Europe", 90003),
        )
        self.assertTrue(
            all("/aggregated/" in request.path for request in http.requests)
        )
        self.assertEqual(len(http.requests), 4)

    async def test_listings_global_quality_request_5_plus_1_bounded_stable_sort(self):
        def callback(request):
            region = request.path.split("/")[-2]
            return currently(
                region,
                rows=[
                    listing(10 + i, region=region, hq=True, ordinal=i) for i in range(6)
                ],
            )

        client, http = self.client(callback)
        execution = await client.execute(
            query(region="global", intent="listings", quality="hq")
        )
        self.assertEqual(len(execution.listings), 5)
        self.assertTrue(execution.truncated)
        self.assertEqual(
            [(row.price_per_unit, row.world_id) for row in execution.listings[:4]],
            [(10, 90002), (10, 90003), (10, 90004), (10, 90005)],
        )
        for request in http.requests:
            parameters = dict(request.query)
            self.assertEqual(
                (parameters["hq"], parameters["listings"], parameters["entries"]),
                ("true", "6", "0"),
            )
            self.assertNotIn("retainer", parameters["fields"])
        self.assertLessEqual(
            len(project_result(execution.result)["document"]["blocks"]), 32
        )

    async def test_partial_failure_empty_429_parse_and_all_error_states(self):
        def callback(request):
            region = request.path.split("/")[-2]
            if region == "North-America":
                return aggregated(region)
            if region == "Europe":
                return SourceHttpError("rate_limited", status_code=429)
            if region == "Japan":
                return {"results": [], "failedItems": []}
            return {"results": [{"itemId": 1}]}

        client, http = self.client(callback)
        execution = await client.execute(query(region="global", intent="min"))
        self.assertIs(execution.result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertIs(execution.outcomes[1].code, ErrorCode.RATE_LIMITED)
        self.assertFalse(execution.outcomes[2].failed)
        self.assertFalse(execution.outcomes[2].data.has_data)
        self.assertIs(execution.outcomes[3].code, ErrorCode.UNPARSED)
        self.assertEqual(len(http.requests), 4)  # no retries or per-World fanout
        client, _ = self.client(
            lambda request: SourceHttpError("rate_limited", status_code=429)
        )
        self.assertIs(
            (await client.execute(query())).result.error.code, ErrorCode.RATE_LIMITED
        )
        client, _ = self.client(
            lambda request: SourceHttpError("upstream_error", status_code=404)
        )
        self.assertIs(
            (await client.execute(query())).result.error.code, ErrorCode.UPSTREAM_ERROR
        )

    async def test_success_empty_is_no_records_without_untradeable_guess(self):
        for payload in (
            {"results": [], "failedItems": []},
            {"results": [{"itemId": 44091, "nq": None, "hq": None}], "failedItems": []},
        ):
            client, _ = self.client(lambda request: payload)
            execution = await client.execute(query())
            self.assertIs(execution.result.error.code, ErrorCode.NO_RECORDS)
            self.assertIn("无法判断", execution.result.error.message)
        client, _ = self.client(lambda request: currently(rows=[]))
        execution = await client.execute(query(intent="listings"))
        self.assertIs(execution.result.error.code, ErrorCode.NO_RECORDS)
        self.assertTrue(execution.outcomes[0].data.has_data_marker)

    async def test_quote_missing_but_other_metrics_present_is_partial_not_zero(self):
        payload = aggregated()
        for quality in ("nq", "hq"):
            payload["results"][0][quality]["minListing"] = None
        client, _ = self.client(lambda request: payload)
        execution = await client.execute(query(intent="min"))
        self.assertIs(execution.result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(execution.minimums, ())
        text = "\n".join(
            getattr(b, "text", "") for b in execution.result.document.ordered_blocks
        )
        self.assertIn("不能补零", text)

    async def test_cache_safe_fields_provenance_and_semantic_keys(self):
        cache = FakeCache()
        client, http = self.client(lambda request: currently(), cache)
        first = await client.execute(query(intent="listings"))
        second = await client.execute(query(intent="listings"))
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(
            first.outcomes[0].provenance.fetched_at,
            second.outcomes[0].provenance.fetched_at,
        )
        self.assertTrue(second.outcomes[0].provenance.cached)
        saved = json.dumps(cache.puts[0][1])
        self.assertNotIn("synthetic-private", saved)
        self.assertNotIn("seller", saved)
        self.assertNotIn("retainer", saved)
        self.assertEqual(cache.puts[0][1]["url"], second.outcomes[0].provenance.url)
        await client.execute(query(intent="listings", quality="nq"))
        self.assertEqual(len(http.requests), 2)
        self.assertNotEqual(cache.puts[0][0], cache.puts[1][0])

    async def test_cache_invalid_metadata_expiry_and_unparsable_entry_are_not_resurrected(
        self,
    ):
        for kind in ("url", "expiry", "body", "future"):
            cache = FakeCache()
            client, http = self.client(lambda request: aggregated(), cache)
            await client.execute(query())
            key, entry = next(iter(cache.values.items()))
            payload = json.loads(json.dumps(cache.puts[0][1]))
            expires = entry.expires_at
            if kind == "url":
                payload["url"] = "https://untrusted.example/"
            if kind == "body":
                payload["body"] = {"results": "wrong"}
            if kind == "expiry":
                expires = NOW - timedelta(seconds=1)
            if kind == "future":
                payload["fetched_at_ms"] = MS + 1000
            cache.values[key] = CacheEntry(key, payload, expires)
            await client.execute(query())
            self.assertEqual(len(http.requests), 2)

    async def test_global_missing_catalog_region_fails_without_shrinking_requests(self):
        partial_catalog = CatalogSnapshot(parse_catalog(WORLDS[:-1], DCS[:-1]), ())
        http = FakeHttp(lambda request: aggregated())
        client = MarketClient(
            MarketSession(http, wall_clock=lambda: NOW), partial_catalog
        )
        with self.assertRaises(QueryResolutionError) as raised:
            await client.execute(query(region="global"))
        self.assertIs(raised.exception.code, ErrorCode.UPSTREAM_ERROR)
        self.assertEqual(http.requests, [])

    async def test_synchronous_parse_and_cache_parse_cannot_return_after_deadline(self):
        now = [0.0]
        http = FakeHttp(lambda request: {"synthetic": True})
        session = MarketSession(http, clock=lambda: now[0], wall_clock=lambda: NOW)

        def late_parser(payload):
            now[0] = 31
            return payload

        with self.assertRaises(MarketDeadlineError):
            await session.read("/api/v2/China/44091", (), late_parser)
        now[0] = 0
        cache = FakeCache()
        session = MarketSession(
            http, cache, clock=lambda: now[0], wall_clock=lambda: NOW
        )
        await session.read("/api/v2/China/44091", (), lambda payload: payload)
        with self.assertRaises(MarketDeadlineError):
            await session.read("/api/v2/China/44091", (), late_parser)

    async def test_aggregate_cache_drops_unselected_layers_and_extra_identity_fields(
        self,
    ):
        payload = aggregated()
        payload["results"][0]["worldUploadTimes"][0]["sellerID"] = "synthetic-private"
        payload["results"][0]["hq"] = "unselected-invalid-shape"
        payload["results"][0]["nq"]["minListing"]["world"] = {
            "price": 1,
            "sellerID": "synthetic-private",
        }
        cache = FakeCache()
        client, _ = self.client(lambda request: payload, cache)
        execution = await client.execute(query(quality="nq"))
        self.assertIs(execution.result.status, ResultStatus.SUCCESS)
        saved = cache.puts[0][1]["body"]
        self.assertNotIn("hq", saved["results"][0])
        self.assertNotIn("world", saved["results"][0]["nq"]["minListing"])
        self.assertNotIn("seller", json.dumps(saved))

    async def test_future_old_unknown_times_are_honest(self):
        payload = aggregated()
        for row in payload["results"][0]["worldUploadTimes"]:
            row["timestamp"] = MS + 5000
        payload["results"][0]["nq"]["recentPurchase"]["region"]["timestamp"] = (
            MS - 86_400_000
        )
        payload["results"][0]["hq"]["recentPurchase"]["region"]["timestamp"] = None
        client, _ = self.client(lambda request: payload)
        execution = await client.execute(query())
        text = "\n".join(
            getattr(b, "text", "") for b in execution.result.document.ordered_blocks
        )
        self.assertIn("源时钟异常", text)
        self.assertIn("86400 秒", text)
        self.assertIn("未知", text)

    async def test_directory_cache_atomic_mapping_clean_and_original_fetched_time(self):
        cache = FakeCache()
        http = FakeHttp(lambda request: WORLDS if request.path == WORLDS_PATH else DCS)
        factory = MarketSourceClient(http, cache, wall_clock=lambda: NOW)
        first = await factory.start().catalog()
        second = await factory.start().catalog()
        self.assertEqual(len(http.requests), 2)
        self.assertEqual(first.catalog, second.catalog)
        self.assertTrue(all(p.cached for p in second.provenance))
        self.assertEqual(
            tuple(p.fetched_at for p in first.provenance),
            tuple(p.fetched_at for p in second.provenance),
        )
        self.assertEqual(len(cache.puts), 1)
        self.assertEqual(
            [request.path for request in http.requests], [WORLDS_PATH, DCS_PATH]
        )
        self.assertEqual(cache.puts[0][2], 300)
        bad = FakeHttp(
            lambda request: WORLDS if request.path == WORLDS_PATH else DCS[:-1]
        )
        empty_cache = FakeCache()
        with self.assertRaises(MarketPayloadError):
            await (
                MarketSourceClient(bad, empty_cache, wall_clock=lambda: NOW)
                .start()
                .catalog()
            )
        self.assertEqual(empty_cache.puts, [])

    async def test_total_deadline_includes_prior_preparation_no_retries_and_keeps_completed(
        self,
    ):
        now = [0.0]

        def callback(request):
            region = request.path.split("/")[-2]
            if region == "Europe":
                now[0] = 31
            return aggregated(region)

        client, http = self.client(callback, clock=lambda: now[0])
        execution = await client.execute(query(region="global"))
        self.assertIs(execution.result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertTrue(execution.outcomes[0].data.has_data)
        self.assertTrue(
            all(outcome.stage == "deadline" for outcome in execution.outcomes[1:])
        )
        self.assertEqual(len(http.requests), 2)
        now = [0.0]
        client, http = self.client(lambda request: aggregated(), clock=lambda: now[0])
        now[0] = 31  # all elapsed preparation/queue time is still part of the session
        execution = await client.execute(query())
        self.assertEqual(len(http.requests), 0)
        self.assertEqual(execution.outcomes[0].reason, "deadline")

    async def test_shared_two_slots_multiple_sessions_queue_and_cancel_drain(self):
        active, peak, started_count, cancelled = 0, 0, 0, 0
        two_started, release = asyncio.Event(), asyncio.Event()

        class Blocking(FakeHttp):
            async def fetch(inner, request):
                nonlocal active, peak, started_count, cancelled
                active += 1
                started_count += 1
                peak = max(peak, active)
                if started_count == 2:
                    two_started.set()
                try:
                    await release.wait()
                    return HttpResponse(
                        200,
                        {},
                        json.dumps(aggregated(request.path.split("/")[-2])).encode(),
                    )
                except asyncio.CancelledError:
                    cancelled += 1
                    raise
                finally:
                    active -= 1

        http = Blocking(None)
        factory = MarketSourceClient(http, wall_clock=lambda: NOW)
        clients = [MarketClient(factory.start(), catalog()) for _ in range(2)]
        tasks = [
            asyncio.create_task(client.execute(query(region="global")))
            for client in clients
        ]
        await two_started.wait()
        self.assertEqual((active, started_count, peak), (2, 2, 2))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual((active, cancelled), (0, 2))
        self.assertTrue(all(task.done() for task in tasks))
        # The same gate is usable after cancellation; no orphan queue/work survives.
        release.set()
        execution = await MarketClient(factory.start(), catalog()).execute(
            query(region="global")
        )
        self.assertIs(execution.result.status, ResultStatus.SUCCESS)
        self.assertLessEqual(peak, 2)

    async def test_source_timeout_sanitized_no_retry_and_cache_denial_not_bypassed(
        self,
    ):
        client, http = self.client(lambda request: SourceHttpError("timeout"))
        result = await client.execute(query())
        self.assertEqual(
            (result.outcomes[0].stage, result.outcomes[0].reason), ("http", "timeout")
        )
        self.assertEqual(len(http.requests), 1)

        class Denied(FakeCache):
            async def get(self, key):
                raise PermissionError("synthetic-sensitive-detail")

        client, http = self.client(lambda request: aggregated(), Denied())
        execution = await client.execute(query())
        self.assertEqual(len(http.requests), 0)
        self.assertIs(execution.result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertNotIn("synthetic-sensitive", execution.result.error.message)

    async def test_manifest_source_policy_and_registered_read_only_market(self):
        root = Path(__file__).resolve().parents[3]
        manifest = json.loads(
            (root / "modules/ff14/yomihime.manifest.json").read_text(encoding="utf-8")
        )
        source = next(
            row
            for row in manifest["modules"][0]["sources"]
            if row["source_id"] == UNIVERSALIS_SOURCE
        )
        self.assertEqual(source["host"], "universalis.app")
        self.assertEqual(source["timeout_seconds"], 10)
        market = next(
            cap
            for cap in manifest["modules"][0]["capabilities"]
            if cap["capability_id"] == "ff14.market.query"
        )
        self.assertEqual(market["effect"], "read_only")
        self.assertEqual(market["invocation_policy"], "command_and_public_web")
        self.assertEqual(market["required_sources"], [UNIVERSALIS_SOURCE])
        from yomihime_sdk.api.manifests import SourceDeclaration

        class Transport:
            async def request(inner, request):
                inner.request_value = request
                return HttpResponse(200, {}, b"[]")

        transport = Transport()
        http = SourceHttpService((SourceDeclaration(**source),), transport)
        await http.fetch(HttpRequest(UNIVERSALIS_SOURCE, WORLDS_PATH))
        self.assertEqual(transport.request_value.url, SOURCE_ROOT + WORLDS_PATH)
        with self.assertRaises(SourceHttpError):
            await http.fetch(HttpRequest("unknown", WORLDS_PATH))

    async def test_cache_age_grows_from_source_time_and_retains_original_fetch(self):
        wall = [NOW]
        cache = FakeCache(wall=lambda: wall[0])
        client, http = self.client(
            lambda request: currently(), cache, wall=lambda: wall[0]
        )
        first = await client.execute(query(intent="listings"))
        wall[0] += timedelta(seconds=20)
        second = await client.execute(query(intent="listings"))
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(
            second.outcomes[0].provenance.fetched_at,
            first.outcomes[0].provenance.fetched_at,
        )
        self.assertEqual(second.observed_at, wall[0])
        text = "\n".join(
            getattr(b, "text", "") for b in second.result.document.ordered_blocks
        )
        self.assertIn("距本次查询 25 秒", text)
        self.assertIn("缓存原获取时间", text)

    async def test_semaphore_wait_uses_independent_session_deadline_and_fresh_bound_ports(
        self,
    ):
        now = [0.0]
        two_started, queued, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        class Blocking(FakeHttp):
            async def fetch(inner, request):
                nonlocal calls
                calls += 1
                if calls == 2:
                    two_started.set()
                await release.wait()
                return HttpResponse(200, {}, json.dumps(aggregated()).encode())

        first_http = Blocking(None)
        second_http = FakeHttp(lambda request: aggregated())
        factory = MarketSourceClient(
            first_http, clock=lambda: now[0], wall_clock=lambda: NOW
        )
        first_session = factory.start()
        first_work = asyncio.create_task(
            MarketClient(first_session, catalog()).execute(query(region="global"))
        )
        await two_started.wait()
        now[0] = 5
        second_session = factory.start_bound(second_http, None)
        original_fetch = second_session._fetch

        async def queued_fetch(path, parameters):
            queued.set()
            return await original_fetch(path, parameters)

        second_session._fetch = queued_fetch
        second_work = asyncio.create_task(
            MarketClient(second_session, catalog()).execute(query(region="global"))
        )
        await queued.wait()
        self.assertEqual((first_session.deadline, second_session.deadline), (30, 35))
        now[0] = 36
        first_work.cancel()
        await asyncio.gather(first_work, return_exceptions=True)
        second = await second_work
        self.assertEqual(second_http.requests, [])
        self.assertTrue(all(outcome.stage == "deadline" for outcome in second.outcomes))
        self.assertEqual(calls, 2)

    async def test_public_http_denial_and_local_authorization_keep_sdk_categories(
        self,
    ):
        for status in (401, 403):
            client, _ = self.client(
                lambda request: SourceHttpError("upstream_error", status_code=status)
            )
            execution = await client.execute(query())
            self.assertIs(execution.result.error.code, ErrorCode.UPSTREAM_ERROR)
            self.assertEqual(execution.outcomes[0].status_code, status)
            self.assertEqual(execution.outcomes[0].stage, "http")
            self.assertEqual(execution.outcomes[0].reason, "upstream_error")

        client, _ = self.client(
            lambda request: SourceHttpError("credentials_unavailable")
        )
        self.assertIs(
            (await client.execute(query())).result.error.code, ErrorCode.AUTH_REQUIRED
        )

        class Expired(PermissionError):
            code = "grant_expired"

        class ExpiredCache(FakeCache):
            async def get(self, key):
                raise Expired("synthetic-private")

        client, _ = self.client(lambda request: aggregated(), ExpiredCache())
        self.assertIs(
            (await client.execute(query())).result.error.code, ErrorCode.AUTH_EXPIRED
        )

    async def test_source_failed_requested_item_is_upstream_unknown_not_schema_error(
        self,
    ):
        for results in ([], None):
            with self.subTest(results=results):
                cache = FakeCache()
                client, _ = self.client(
                    lambda request: {"results": results, "failedItems": [44091]}, cache
                )
                execution = await client.execute(query())
                self.assertIs(execution.result.error.code, ErrorCode.UPSTREAM_ERROR)
                self.assertEqual(execution.outcomes[0].stage, "source")
                self.assertEqual(execution.outcomes[0].reason, "unresolved_item")
                self.assertIn("无法", execution.result.error.message)
                self.assertEqual(cache.puts, [])

    async def test_failed_items_schema_identity_and_success_conflict_stay_unparsed(
        self,
    ):
        for payload in (
            {"failedItems": [44091]},
            {"results": "invalid", "failedItems": [44091]},
            {"results": [], "failedItems": [44092]},
            {"results": [], "failedItems": [True]},
            {"results": [], "failedItems": [44091, 44091]},
            {"results": [{"itemId": 44091}], "failedItems": [44091]},
            {"results": None, "failedItems": []},
        ):
            with self.subTest(payload=payload):
                client, _ = self.client(lambda request: payload)
                execution = await client.execute(query())
                self.assertIs(execution.result.error.code, ErrorCode.UNPARSED)
                self.assertEqual(execution.outcomes[0].stage, "parse")
