"""Offline projections from normalized source facts, never production sample imports."""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import fields, is_dataclass, replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.modules.ff14.features.item_cards import build_item_card
from ygl_test_subject.modules.ff14.features.items import _item_result
from ygl_test_subject.modules.ff14.features.market import _result
from ygl_test_subject.modules.ff14.features.market_cards import build_market_card
from ygl_test_subject.modules.ff14.features.market_models import (
    Listing,
    Quote,
    ScopeData,
    ScopeOutcome,
)
from ygl_test_subject.modules.ff14.features.market_sources import Provenance
from ygl_test_subject.modules.ff14.models import (
    AcquisitionKind,
    AcquisitionRoute,
    ItemAmount,
    ItemRecord,
)

from tests.fixtures.ff14.item_display import source_snapshot
from tests.modules.ff14.test_market import NOW, catalog, query
from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.display import Privacy
from yomihime_game_link_sdk.results import ErrorCode, ResultStatus

ROOT = Path(__file__).resolve().parents[3]
ITEM_URLS = (
    "https://xivapi-v2.xivcdn.com/api/sheet/Item/44091",
    "https://garlandtools.cn/db/#item/44091",
)


def text_values(value):
    """Inspect declarations as data, without importing any renderer or font."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, tuple):
        for item in value:
            yield from text_values(item)
    elif is_dataclass(value):
        for field in fields(value):
            yield from text_values(getattr(value, field.name))


def text(value):
    return "\n".join(text_values(value))


def item_snapshot():
    # The approved visible names/counts; named material IDs below are synthetic
    # test identities, not a claimed capture from an upstream API.
    costs = tuple(
        ItemAmount(90001 + index, count, name)
        for index, (name, count) in enumerate(
            (
                ("图拉尔玉米油", 1),
                ("高山食盐", 1),
                ("犎牛肩肉", 1),
                ("龙息椒", 1),
                ("亚斯拉尼蒜头", 2),
                ("黑夜醋", 1),
            )
        )
    ) + (ItemAmount(8, 8), ItemAmount(13, 8))
    cost_summary = "材料/货币：" + "、".join(
        f"{cost.name or f'物品 ID {cost.item_id}'} × {cost.amount}" for cost in costs
    )
    return ItemRecord(
        44091,
        "犎牛牛排",
        item_level=684,
        equip_level=1,
        routes=(
            AcquisitionRoute(
                AcquisitionKind.QUEST,
                "任務",
                ("缇姨家的塔可饼的老主顾", "记录 ID：70996", "区域：图莱尤拉"),
            ),
            AcquisitionRoute(
                AcquisitionKind.CRAFT,
                "制作",
                ("职业 ID：15", "等级：99", cost_summary),
                costs,
                partial=True,
            ),
        ),
        source_urls=ITEM_URLS,
        warnings=("Garland 的制作关联信息不完整。",),
    )


def outcome(*quotes, region="China", cached=False):
    return ScopeOutcome(
        region,
        region,
        ScopeData(quotes=tuple(quotes)),
        Provenance(
            f"https://universalis.app/api/v2/aggregated/{region}/44091",
            NOW - timedelta(minutes=2),
            cached=cached,
        ),
    )


class ItemCardTests(unittest.IsolatedAsyncioTestCase):
    def test_item_44091_details_costs_warnings_and_full_fallback_links(self):
        record = item_snapshot()
        result = _item_result(record, partial=True)
        self.assertIsInstance(result.document.card, cards.Document)
        self.assertEqual(result.document.card.title, "犎牛牛排")
        self.assertIs(result.document.privacy, Privacy.PUBLIC)
        self.assertIsNone(result.model_facts)
        content = text(result.document.card)
        for value in (
            "44091",
            "684",
            "1",
            "获取途径",
            "制作材料",
            *record.routes[0].details,
        ):
            self.assertIn(value, content)
        for cost in record.routes[1].costs:
            self.assertIn(
                f"{cost.name or f'物品 ID {cost.item_id}'}  × {cost.amount}", content
            )
        for detail in record.routes[1].details[:2]:
            self.assertIn(detail, content)
        for warning in record.warnings:
            self.assertIn(warning, content)
        self.assertEqual(
            tuple(link.url for link in result.document.ordered_blocks[-1].links),
            ITEM_URLS,
        )
        self.assertNotIn("https://", content)
        self.assertEqual(result.document.ordered_blocks[0].text, "物品 ID：44091")
        self.assertIn(
            record.routes[1].details[-1], result.document.ordered_blocks[4].text
        )

    async def test_all_bounded_routes_details_and_warnings_remain_in_card(self):
        snapshot = await source_snapshot((6, 6, 6, 6, 6, 6), extra_warnings=True)
        content = text(build_item_card(snapshot.record))
        self.assertEqual(len(snapshot.record.routes), 24)
        for route in snapshot.record.routes:
            self.assertIn(route.label, content)
            for detail in route.details:
                self.assertIn(detail, content)
        for warning in snapshot.record.warnings:
            self.assertIn(warning, content)
        self.assertIn(snapshot.record.description, content)

    def test_unknown_levels_and_routes_do_not_become_zero_or_unavailable_claims(self):
        card = build_item_card(ItemRecord(77, "Other item", source_urls=ITEM_URLS))
        grid = card.blocks[0]
        self.assertEqual(
            [prop.value for prop in grid.properties], ["77", "未提供", "未提供"]
        )
        self.assertIn("这不代表物品不可获得", text(card))
        self.assertNotIn("犎牛牛排", text(card))

    def test_bundled_logo_resource_is_pinned_and_bytes_only(self):
        manifest = json.loads(
            (ROOT / "modules/ff14/yomihime.manifest.json").read_text(encoding="utf-8")
        )
        module = manifest["modules"][0]
        relative = module["display"]["logo_resource"]
        content = (ROOT / "modules/ff14" / relative).read_bytes()
        entry = next(row for row in module["resources"] if row["path"] == relative)
        self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
        self.assertLess(len(content), 64 * 1024)
        self.assertIsNone(build_item_card(item_snapshot()).logo)

    def test_projection_does_not_read_files_to_resolve_registered_branding(self):
        with patch(
            "builtins.open",
            side_effect=AssertionError("module display cannot read files"),
        ):
            result = _item_result(item_snapshot(), partial=True)
            self.assertIsNone(result.document.card.logo)
            self.assertEqual(result.document.card.title, item_snapshot().name)

    def test_only_equivalent_craft_partial_warning_is_deduplicated(self):
        record = item_snapshot()
        content = text(build_item_card(record))
        self.assertEqual(content.count("Garland 的制作关联信息不完整。"), 1)
        self.assertNotIn("部分信息；来源关联尚不完整", content)
        unique = "Garland 的任务列表已截断。"
        content = text(
            build_item_card(replace(record, warnings=(*record.warnings, unique)))
        )
        self.assertIn(unique, content)
        self.assertIn(
            "部分信息；来源关联尚不完整",
            text(build_item_card(replace(record, warnings=()))),
        )
        multiple = replace(
            record, routes=(*record.routes, replace(record.routes[1], partial=False))
        )
        # With multiple recipes the generic source warning does not identify
        # which recipe is partial, so its per-route marker remains necessary.
        self.assertEqual(
            text(build_item_card(multiple)).count("部分信息；来源关联尚不完整"), 1
        )


class MarketCardTests(unittest.TestCase):
    def test_minimums_show_only_available_results_and_explicit_unknown_quality(self):
        request = query(intent="min")
        nq = Quote(
            "nq",
            minimum=30,
            minimum_world=90001,
            minimum_uploaded_at=NOW - timedelta(minutes=12),
        )
        rows = (outcome(nq, Quote("hq"), cached=True),)
        result = _result(
            request, rows, (), (("nq", "China", nq),), False, NOW, catalog()
        )
        content = text(result.document.card)
        self.assertEqual(result.document.card.title, "物品 44091")
        self.assertIn("30 Gil / 件", content)
        self.assertIn("SyntheticChina", content)
        self.assertIn("12 分钟", content)
        self.assertIn("来源获取：中国 2 分钟前（缓存）", content)
        self.assertIn("HQ：最低挂牌未知", content)
        self.assertNotIn("0 Gil / 件", content.splitlines())
        self.assertEqual(
            sum(
                isinstance(block, cards.Section) and block.title == ""
                for block in result.document.card.blocks
            ),
            1,
        )
        self.assertIn(rows[0].provenance.url, text(result.document.ordered_blocks))
        self.assertNotIn("https://", content)
        self.assertIs(result.document.privacy, Privacy.PUBLIC)

    def test_verified_name_only_and_single_requested_quality(self):
        request = replace(query(intent="min", quality="hq"), item_name="Source name")
        hq = Quote("hq", minimum=500, minimum_world=90001)
        card = build_market_card(
            request, (outcome(hq),), (), (("hq", "China", hq),), False, NOW, catalog()
        )
        self.assertEqual(card.title, "Source name")
        self.assertIn("世界上传：未知", text(card))
        self.assertNotIn("NQ", text(card))
        self.assertNotIn("犎牛牛排", text(card))

    def test_all_missing_data_is_error_instead_of_two_fake_price_results(self):
        request = query(intent="min")
        result = _result(
            request, (outcome(Quote("nq"), Quote("hq")),), (), (), False, NOW, catalog()
        )
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertIsNone(result.document)
        self.assertIs(result.error.code, ErrorCode.NO_RECORDS)

    def test_recent_sale_only_is_not_substituted_for_minimum(self):
        request = query(intent="min")
        result = _result(
            request,
            (outcome(Quote("nq", recent_purchase=41)),),
            (),
            (),
            False,
            NOW,
            catalog(),
        )
        content = text(result.document.card)
        self.assertNotIn("41 Gil", content)
        self.assertNotIn("0 Gil / 件", content.splitlines())
        self.assertIn("来源未提供最低挂牌指标", content)
        self.assertIn("HQ：最低挂牌未知", content)

    def test_overview_preserves_each_quality_scope_and_metric(self):
        request = query(region="global", intent="overview")
        rows = tuple(
            outcome(
                Quote(
                    "nq",
                    minimum=10 + index,
                    recent_purchase=20 + index,
                    average_sale_price=30.25 + index,
                    daily_sale_velocity=40.5 + index,
                ),
                Quote("hq", recent_purchase=50 + index),
                region=region,
            )
            for index, region in enumerate(request.scope.regions)
        )
        card = build_market_card(request, rows, (), (), False, NOW, catalog())
        content = text(card)
        panels = [block for block in card.blocks if isinstance(block, cards.Section)]
        self.assertEqual(len(panels), 2 * len(request.scope.regions))
        region_labels = {
            "North-America": "北美",
            "Europe": "欧洲",
            "Japan": "日本",
            "Oceania": "大洋洲",
        }
        for index, region in enumerate(request.scope.regions):
            nq, hq = panels[index * 2 : index * 2 + 2]
            for panel, quality in ((nq, "NQ"), (hq, "HQ")):
                values = list(text_values(panel))
                self.assertEqual(values.count(quality), 1)
                self.assertEqual(text(panel).count(region_labels[region]), 1)
            for value in (20 + index, 30.25 + index, 40.5 + index):
                self.assertIn(str(value), text(nq))
            self.assertIn(f"{50 + index} Gil", text(hq))
        self.assertIn("未知 Gil / 件", content)
        self.assertNotIn("0 Gil / 件", content.splitlines())
        self.assertIn("近四天", content)

    def test_listings_keep_existing_limited_rows_and_quantity_semantics(self):
        request = query(intent="listings")
        listings = tuple(
            Listing(
                101 + index, 7 + index, index % 2 == 1, 90001, "China", NOW, None, index
            )
            for index in range(5)
        )
        row = ScopeOutcome(
            "China",
            "China",
            ScopeData(listings=listings),
            Provenance("https://universalis.app/api/v2/China/44091", NOW),
        )
        card = build_market_card(request, (row,), listings, (), True, NOW, catalog())
        content = text(card)
        table = card.blocks[0]
        self.assertIsInstance(table, cards.Table)
        self.assertEqual(len(table.rows), 5)
        self.assertEqual(
            [column.title for column in table.columns],
            ["品质", "单价 / Gil", "数量 / 堆", "服务器", "来源时间"],
        )
        for source, displayed in zip(listings, table.rows, strict=True):
            self.assertEqual(
                displayed[:4],
                (
                    "HQ" if source.hq else "NQ",
                    str(source.price_per_unit),
                    str(source.quantity),
                    "SyntheticChina",
                ),
            )
            self.assertEqual(displayed[4], "上传：未知\n审核：刚刚")
        for caveat in (
            "最多 5 条",
            "已截断",
            "不是全服库存",
            "不代表上架时间",
            "非实时全量报价",
            "不保证此刻可买",
        ):
            self.assertIn(caveat, content)
        self.assertNotIn("seller", content)
        self.assertNotIn("retainer", content)

    def test_failure_coverage_unknown_world_and_future_time_remain_explicit(self):
        request = query(region="global", intent="min", quality="nq")
        quote = Quote(
            "nq",
            minimum=2,
            minimum_world=90001,
            minimum_uploaded_at=NOW + timedelta(hours=1),
        )
        rows = (
            outcome(quote, region="Europe"),
            ScopeOutcome(
                "Japan",
                "Japan",
                code=ErrorCode.UPSTREAM_ERROR,
                stage="source",
                reason="offline",
            ),
        )
        card = build_market_card(
            request, rows, (), (("nq", "Europe", quote),), False, NOW, catalog()
        )
        content = text(card)
        self.assertIn("未知服务器（ID 90001）", content)
        self.assertNotIn("SyntheticChina", content)
        self.assertIn("时间异常（晚于查询）", content)
        self.assertIn("日本 来源查询失败", content)

    def test_card_uses_readable_labels_and_compact_provenance(self):
        request = query(intent="min", region="cn")
        quote = Quote("nq", minimum=17, minimum_world=90001)
        row = outcome(quote)
        card = build_market_card(
            request, (row,), (), (("nq", "China", quote),), False, NOW, catalog()
        )
        content = text(card)
        self.assertEqual(card.subtitle, "物品 44091 · 中国全区 · 最低挂牌")
        for internal in (
            "explicit",
            "default",
            "ALL",
            "缺值写未知",
            "不补零",
            "成交价不替代挂牌价",
            NOW.isoformat(),
        ):
            self.assertNotIn(internal, content)
        self.assertEqual(len(card.sources), 1)
        self.assertIn("Universalis", card.sources[0])
        self.assertIn("来源获取：中国 2 分钟前", card.sources[0])

    def test_compact_coverage_preserves_partial_empty_and_failure_states(self):
        request = query(region="global", intent="min")
        quote = Quote("nq", minimum=3)
        partial = replace(
            outcome(quote, region="Europe"),
            data=ScopeData(quotes=(quote,), incomplete=True),
        )
        empty = outcome(region="Japan")
        failure = ScopeOutcome(
            "Oceania",
            "Oceania",
            code=ErrorCode.RATE_LIMITED,
            stage="http",
            reason="rate_limited",
        )
        card = build_market_card(
            request,
            (partial, empty, failure),
            (),
            (("nq", "Europe", quote),),
            False,
            NOW,
            catalog(),
        )
        for state in (
            "欧洲 2 分钟前，部分指标缺失",
            "日本 2 分钟前，无可用记录",
            "大洋洲 请求限流",
        ):
            self.assertIn(state, card.sources[0])
