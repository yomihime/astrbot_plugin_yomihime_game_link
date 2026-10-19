"""Real-shape synthetic facts exercise cards without network or screenshot data."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.modules.ff14.features.calendar_evaluator import (
    build_calendar_document,
)
from ygl_test_subject.modules.ff14.features.calendar_models import CalendarOccurrence
from ygl_test_subject.modules.ff14.features.item_cards import build_item_card
from ygl_test_subject.modules.ff14.features.item_sources import (
    ItemPayloadError,
    parse_garland_acquisition,
    parse_garland_effects,
    parse_xivapi_item_detail,
)
from ygl_test_subject.modules.ff14.features.status_cards import (
    CALENDAR_DEFAULT_INTERVAL_SECONDS,
    build_status_document,
)

from tests.modules.ff14.test_item_market_cards import text
from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.display import Privacy
from yomihime_game_link_sdk.subscriptions import ObservationCompleteness


class ResultCardTests(unittest.TestCase):
    def detail(self, description, *, name="Synthetic meal", item_id=77):
        return parse_xivapi_item_detail(
            {"row_id": item_id, "fields": {"Name": name, "Description": description}},
            requested_id=77,
        )

    def test_description_only_accepts_bounded_normalized_newlines(self):
        for source in (
            "Description\nBonus\nDuration",
            "Description\r\nBonus\rDuration",
        ):
            record = self.detail(source)
            self.assertEqual(record.description, "Description\nBonus\nDuration")
            description_blocks = [
                block.content
                for block in build_item_card(record).blocks
                if isinstance(block, cards.Text)
            ]
            self.assertEqual("\n".join(description_blocks[:2]), record.description)
        self.assertEqual(len(self.detail("x" * 1000).description), 1000)
        for source in (
            "x" * 1001,
            "A\tB",
            "A\t",
            "\x85A",
            "A\x00B",
            "A\x1bB",
            "A\x7fB",
            "A\x85B",
            "\n",
            42,
        ):
            self.assertIsNone(self.detail(source).description)
        for name in ("Bad\nName", "Bad\rName", "Bad\tName"):
            with self.assertRaises(ItemPayloadError):
                self.detail("Valid\nDescription", name=name)
        with self.assertRaises(ItemPayloadError):
            self.detail("Valid\nDescription", item_id=78)

    def test_optional_effects_and_recipe_facts_never_infer_missing_quality(self):
        payload = {
            "item": {
                "id": 77,
                "attr": {
                    "action": {
                        "Craftsmanship": {"rate": 12, "limit": 34},
                        "Invalid": {"rate": True, "limit": 10},
                    }
                },
                "craft": [
                    {
                        "id": 98,
                        "job": 15,
                        "lvl": 99,
                        "yield": 3,
                        "ingredients": [{"id": 42, "amount": 2}],
                    }
                ],
            },
            "partials": [],
        }
        effects = parse_garland_effects(payload, requested_id=77)
        self.assertEqual(len(effects), 1)
        routes, warnings = parse_garland_acquisition(payload, requested_id=77)
        record = replace(
            self.detail("Meal\nDuration"),
            effects=effects,
            routes=routes,
            warnings=warnings,
        )
        card = build_item_card(record)
        validate_contract(card)
        content = text(card)
        for expected in (
            "食用效果",
            "+12%（上限 34）",
            "未提供",
            "配方 ID：98",
            "产量：3",
            "物品 ID 42  × 2",
        ):
            self.assertIn(expected, content)
        self.assertNotIn("Invalid", content)
        self.assertNotIn("食用效果", text(build_item_card(self.detail(None))))
        payload["item"]["attr_hq"] = {
            "action": {"Craftsmanship": {"rate": 15, "limit": 45}}
        }
        self.assertEqual(len(parse_garland_effects(payload, requested_id=77)), 2)

    def test_quest_association_does_not_claim_reward_or_requirement(self):
        payload = {
            "item": {"id": 77, "quests": [12]},
            "partials": [
                {"type": "quest", "id": 12, "obj": {"n": "Related quest", "l": "Town"}}
            ],
        }
        routes, warnings = parse_garland_acquisition(payload, requested_id=77)
        self.assertEqual(routes[0].label, "关联任务")
        card = build_item_card(
            replace(self.detail(None), routes=routes, warnings=warnings)
        )
        sections = [
            block.title for block in card.blocks if isinstance(block, cards.Section)
        ]
        self.assertEqual(sections, ["关联任务"])
        self.assertIn("来源关联不表示任务奖励或提交要求", text(card))
        self.assertEqual(card.sources, ())
        self.assertIn("数据源：", card.blocks[-1].content)

    def test_effect_bounds_and_identity(self):
        payload = {
            "item": {
                "id": 77,
                "attr": {
                    "action": {
                        f"Attribute {n}": {"rate": n, "limit": n} for n in range(20)
                    }
                },
            }
        }
        self.assertEqual(len(parse_garland_effects(payload, requested_id=77)), 8)
        with self.assertRaises(ItemPayloadError):
            parse_garland_effects(payload, requested_id=78)

    def calendar(self, occurrences=(), **kwargs):
        params = dict(
            local_date=date(2030, 2, 3),
            days=7,
            timezone_name="Asia/Shanghai",
            now=datetime(2030, 2, 3, 5, tzinfo=UTC),
            source_variant="cn_google",
            privacy=Privacy.PUBLIC,
        )
        params.update(kwargs)
        return build_calendar_document(occurrences, **params)

    def test_calendar_dynamic_range_ongoing_upcoming_end_and_timezone(self):
        now = datetime(2030, 2, 3, 5, tzinfo=UTC)
        events = tuple(
            CalendarOccurrence(
                str(n),
                str(n),
                f"Event {n}",
                now + timedelta(days=start),
                now + timedelta(days=end),
                False,
                False,
                "test",
            )
            for n, start, end in ((1, -30, 3), (2, 1, 10))
        )
        doc = self.calendar(events)
        validate_contract(doc)
        content = text(doc.card)
        for expected in (
            "2030.02.03 — 2030.02.09",
            "Asia/Shanghai",
            "进行中",
            "尚未开始",
            "2030-02-06 13:00",
            "2030-02-13 13:00",
            "来源更新时间未知",
            "不保证覆盖全部活动",
        ):
            self.assertIn(expected, content)
        self.assertEqual(
            len([b for b in doc.card.blocks if isinstance(b, cards.Section)]), 2
        )
        self.assertIn("Event 1", text(doc.ordered_blocks))
        self.assertNotIn("2026", content)

    def test_calendar_all_day_exclusive_end_partial_empty_and_truncation(self):
        event = CalendarOccurrence(
            "a", "a", "All day", date(2030, 2, 1), date(2030, 2, 5), True, False, "test"
        )
        content = text(self.calendar((event,)).card)
        self.assertIn("2030-02-04（全天）", content)
        self.assertNotIn("排他结束", content)
        empty = self.calendar(completeness=ObservationCompleteness.PARTIAL)
        self.assertIn("无法确认当前窗口是否无活动", text(empty.card))
        many = self.calendar(
            tuple(replace(event, uid=str(n), summary=f"Event {n}") for n in range(18))
        )
        validate_contract(many)
        self.assertIn("另有 3 项活动未显示", text(many.card))

    def test_status_is_static_capabilities_not_health_or_credentials(self):
        doc = build_status_document()
        validate_contract(doc)
        content = text(doc.card)
        for expected in (
            "市场查询",
            "默认采集间隔",
            "15 分钟",
            "未执行实时健康或凭据检查",
            "实际可能延迟",
        ):
            self.assertIn(expected, content)
        for absent in ("Ready", "HTTP 403", "输出统计", "已配置", "最多"):
            self.assertNotIn(absent, content)
        manifest = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "modules/ff14/yomihime.manifest.json"
            ).read_text(encoding="utf-8")
        )

        def values(node):
            if isinstance(node, dict):
                if "default_interval_seconds" in node:
                    yield node["default_interval_seconds"]
                for child in node.values():
                    yield from values(child)
            elif isinstance(node, list):
                for child in node:
                    yield from values(child)

        self.assertIn(CALENDAR_DEFAULT_INTERVAL_SECONDS, tuple(values(manifest)))


class ResultCardSourceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_carries_effects_without_extra_requests_or_display_blocks(
        self,
    ):
        from ygl_test_subject.modules.ff14.features.item_sources import ItemSourceClient
        from ygl_test_subject.modules.ff14.features.items import _item_result

        from tests.fixtures.ff14.item_display import source_responses
        from tests.modules.ff14.test_items import _FakeHttp

        primary, garland = source_responses(extra_warnings=True)
        primary["fields"]["Description"] = "Synthetic meal\r\nDuration"
        for field in ("attr", "attr_hq"):
            garland["item"][field] = {
                "action": {
                    f"Attribute {n}": {"rate": n, "limit": n + 10} for n in range(8)
                }
            }
        http = _FakeHttp([primary, garland])
        snapshot = await ItemSourceClient(http).lookup(90001)
        result = _item_result(snapshot.record, partial=snapshot.partial)
        validate_contract(result)
        self.assertEqual(len(http.requests), 2)
        self.assertEqual(len(snapshot.record.effects), 16)
        self.assertLessEqual(len(result.document.ordered_blocks), 32)
        self.assertIn("Attribute 7", text(result.document.ordered_blocks))
        self.assertEqual(snapshot.record.description, "Synthetic meal\nDuration")
