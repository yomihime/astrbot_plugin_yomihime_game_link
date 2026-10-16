#!/usr/bin/env python3
"""Review fixtures expressed only as declarations for the shared Core renderer.

No API query, production registry, Pillow layout, alternate painter, or old
example renderer is used. Help availability is an explicit offline fixture;
labels/routes come from the bundled FF14 manifest. Item facts transcribe the
user's supplied screenshot. Logs are the existing synthetic layout dataset,
with raw-value grading checked separately by tests. Provenance stays alongside
output PNGs, not inside their visual design. The five user-approved visual designs remain immutable offline fixtures.

From the repository root:
    python -m examples.command_cards.declarative_samples --output-dir /tmp/cards
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from unicodedata import category

from presentation.card_bitmap import RenderedBitmap
from presentation.layout_renderer import render_document
from yomihime_game_link_sdk.cards import (
    Asset,
    Column,
    Document,
    Image,
    Inline,
    Property,
    PropertyGrid,
    Row,
    Section,
    Span,
    Table,
    Text,
)

ROOT = Path(__file__).resolve().parents[2]
BRAND = "如月怜的游戏连结"
FOOTER = "Powered by yomihime/astrbot_plugin_yomihime_game_link"
ITEM_SOURCE_URLS = (
    "https://xivapi-v2.xivcdn.com/api/sheet/Item/44091",
    "https://garlandtools.cn/db/#item/44091",
)


@dataclass(frozen=True)
class CatalogModule:
    """Caller-owned availability facts; never a production registration API."""

    key: str
    name: str
    route: str
    commands: tuple[str, ...]
    summary: str = ""
    loaded: bool = True
    enabled: bool = True
    available: bool = True


def help_catalog_fixture() -> tuple[CatalogModule, ...]:
    """One explicitly available fixture using the repository's real manifest.

    This does not inspect or assert the availability of a running deployment.
    Future production code must supply its coherent active catalog snapshot.
    """
    manifest = json.loads(
        (ROOT / "modules/ff14/yomihime.manifest.json").read_text(encoding="utf-8")
    )
    return tuple(
        CatalogModule(
            key=f"{manifest['package_id']}/{module['module_id']}",
            name=module.get("display", {}).get("default_name", module["route"]),
            route=module["route"],
            commands=tuple(c["operation_path"] for c in module["commands"]),
            summary=" · ".join(
                label
                for operation, label in (
                    ("item", "物品"),
                    ("market", "市场"),
                    ("logs", "战绩"),
                    ("calendar", "活动日历"),
                )
                if any(c["operation_path"] == operation for c in module["commands"])
            ),
        )
        for module in manifest["modules"]
    )


def meteor_asset() -> Asset:
    """The caller loads one trusted local fixture; Core accepts only bytes."""
    return Asset(
        "svg",
        (Path(__file__).parent / "assets/ff14-meteor.svg").read_bytes(),
        alt="最终幻想 XIV 陨石徽标",
    )


def build_help_document(
    catalog: tuple[CatalogModule, ...] | None = None,
    *,
    seed: int | None = None,
) -> Document:
    """Map a supplied available catalog to a declarative module directory."""
    supplied = help_catalog_fixture() if catalog is None else catalog
    active = {
        module.key: module
        for module in supplied
        if module.loaded and module.enabled and module.available
    }
    blocks = []
    for module in sorted(active.values(), key=lambda item: item.route):
        text = Section(
            module.name,
            (
                Text(module.summary or "查看模块命令与用法"),
                Text(f"/ygl {module.route} help", style="heading"),
            ),
            subtitle="FF14" if module.route == "ff14" else module.route,
            panel=False,
        )
        # The asset association belongs to this FF14 example, not Core.
        contents = (
            (Row((Image(meteor_asset(), width=128, height=128), text), weights=(1, 5)),)
            if module.route == "ff14"
            else (text,)
        )
        blocks.append(Section("", contents))
    if not blocks:
        blocks.append(Section("暂无可用模块", (Text("模块就绪后会在这里出现"),)))
    candidates = sorted(active.values(), key=lambda item: item.route)
    recommended = random.Random(seed).choice(candidates) if candidates else None
    if recommended is None:
        subtitle = "输入 /ygl help，查看当前可用模块"
    elif recommended.route == "ff14":
        subtitle = "从最终幻想XIV开始吧"
    else:
        subtitle = f"从{recommended.name}开始吧"
    return Document(
        title="游戏，从这里连结",
        brand=BRAND,
        subtitle=subtitle,
        blocks=tuple(blocks),
        layout="directory",
        theme="warm_paper",
    )


ITEM_MATERIALS = (
    ("图拉尔玉米油", "× 1"),
    ("高山食盐", "× 1"),
    ("犎牛肩肉", "× 1"),
    ("龙息椒", "× 1"),
    ("亚斯拉尼蒜头", "× 2"),
    ("黑夜醋", "× 1"),
    ("物品 ID 8", "× 8"),
    ("物品 ID 13", "× 8"),
)


def build_item_document() -> Document:
    """Approved item visual design, preserving screenshot facts and omissions."""
    return Document(
        title="犎牛牛排",
        subtitle="最终幻想 XIV · 物品详情",
        brand="游戏连结 · FF14 · 物品",
        logo=meteor_asset(),
        theme="warm_paper",
        layout="detail",
        blocks=(
            PropertyGrid(
                (
                    Property("物品 ID", "44091"),
                    Property("物品等级", "684"),
                    Property("装备等级", "1"),
                ),
                columns=3,
            ),
            Section(
                "获取途径",
                (
                    Text("缇姨家的塔可饼的老主顾", style="heading"),
                    Text("任务 · 图莱尤拉 · 记录 ID 70996", style="caption"),
                ),
                subtitle="已记录的途径；缺少关联不代表不可获得",
            ),
            Section(
                "制作材料",
                (
                    *(
                        Row(
                            tuple(
                                Text(f"{name}  {quantity}") for name, quantity in pair
                            )
                        )
                        for pair in zip(
                            ITEM_MATERIALS[::2], ITEM_MATERIALS[1::2], strict=True
                        )
                    ),
                    Text("Garland 制作关联信息不完整", style="caption"),
                ),
                subtitle="职业 ID 15 · 等级 99",
            ),
        ),
        sources=("数据源：XIVAPI-compatible (xivcdn) · Garland Tools CN",),
    )


# Historical market illustration, not a new Universalis quote or stock promise.
MARKET_FIXTURE = (
    ("NQ", "30", "静语庄园", "世界上传于 12 分钟前"),
    ("HQ", "500", "神拳痕", "世界上传于 2 小时前"),
)


def build_market_document() -> Document:
    """Render the historical market fixture through the same shared Core."""
    return Document(
        title="犎牛牛排",
        subtitle="物品 44091 · 中国全区",
        brand="游戏连结 · FF14 · 市场",
        logo=meteor_asset(),
        theme="warm_paper",
        layout="detail",
        blocks=(
            *(
                Section(
                    "",
                    (
                        Row(
                            (
                                Text(quality, style="heading"),
                                Text(f"{price} Gil / 件", style="title", wrap="nowrap"),
                                Section(
                                    "",
                                    (
                                        Text(world, style="heading"),
                                        Text(upload_age),
                                    ),
                                    panel=False,
                                ),
                            ),
                            weights=(0.55, 1.4, 1.85),
                        ),
                    ),
                )
                for quality, price, world, upload_age in MARKET_FIXTURE
            ),
            Text("有限返回样本中的最低价；非实时全量报价，不保证此刻仍可买到"),
            Text("时间为对应世界的上传时间，并非该条挂牌时间", style="caption"),
        ),
        sources=("数据源：Universalis",),
    )


@dataclass(frozen=True)
class Unevaluable:
    """Validated source text, distinct from missing data and malformed numbers."""

    text: str

    def __post_init__(self):
        if (
            type(self.text) is not str
            or not 0 < len(self.text) <= 512
            or any(
                category(c).startswith("C") or c in "\u2028\u2029" for c in self.text
            )
        ):
            raise ValueError("Invalid bounded unevaluable text")


@dataclass(frozen=True)
class Record:
    encounter: str
    best_percentile: object
    job: str
    highest_rdps: object
    total_kills: object
    version: str


# All records/catalog labels are offline layout fixtures, not a live catalog,
# captured API response, or real character. Adapted from ff14_logs_reference.py;
# no import of that file, its renderer, or its independent layout is necessary.
ULTIMATE = (
    Record("妖星绝境战", "100", "武士", "41234.567890123", 31, "7.51"),
    Record("光暗未来绝境战", "99.3", "占星术士", "16123.456789012", 46, "7.4"),
    Record("欧米茄绝境验证战", "95", "白魔法师", "6712.5", 18, "6.5"),
    Record("幻想龙诗绝境战", "75", "白魔法师", "3821.49", 24, "6.28"),
    Record("亚历山大绝境战", "50", "白魔法师", "6321.22", 9, "5.x"),
    Record("究极神兵绝境战", "25", "赤魔法师", "5543.78", 6, "5.4"),
    Record("巴哈姆特绝境战", "0", "赤魔法师", "6032.12", 2, "5.4"),
)
SAVAGE = (
    Record("M12S 本体\n林德布鲁姆 II", "99.7", "学者", "23123.23", 12, "7.4"),
    Record("M12S 门神\n林德布鲁姆", "96.4", "学者", "24123.35", 16, "7.4"),
    Record("M11S 霸王", "82.1", "学者", "23210.46", 21, "7.4"),
    Record("M10S 极限兄弟", "61.7", "学者", "22310.57", 17, "7.4"),
    Record("M9S 致命美人", None, "—", None, 0, "—"),
    Record("M8S 剑嚎", "90.4", "占星术士", "18123.79", 13, "7.2"),
    Record("M7S 野蛮恨心", "93.6", "占星术士", "18810.89", 14, "7.2"),
    Record("M6S 狂热糖潮", "74.2", "占星术士", "19710.91", 22, "7.2"),
    Record("M5S 热舞绿光", "89.8", "占星术士", "17810.12", 11, "7.2"),
    Record("M4S 狡雷", "98.6", "贤者", "15610.23", 24, "7.0"),
    Record("M3S 野蛮爆弹狂人", "95.8", "贤者", "14810.35", 17, "7.0"),
    Record("M2S 蜂蜂小甜心", "99.4", "贤者", "15810.46", 21, "7.0"),
    Record("M1S 黑猫", "98.4", "贤者", "15410.57", 30, "7.0"),
)
PERCENTILE_GRADES = (
    (Decimal("100"), "gold"),
    (Decimal("99"), "#e268a8"),
    (Decimal("95"), "#ff8000"),
    (Decimal("75"), "#a335ee"),
    (Decimal("50"), "#0070ff"),
    (Decimal("25"), "#1eff00"),
    (Decimal("0"), "#666666"),
)
MAX_NUMERIC_CHARS = 128
MAX_INTEGER_DIGITS = 18


def _number(raw: object) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise ValueError("Invalid metric type")
    if isinstance(raw, int) and raw.bit_length() > 64:
        raise ValueError("Metric exceeds numeric input budget")
    if isinstance(raw, Decimal):
        if not raw.is_finite() or len(raw.as_tuple().digits) > MAX_NUMERIC_CHARS:
            raise ValueError("Invalid or oversized Decimal")
        value = raw
    else:
        source = str(raw)
        if len(source) > MAX_NUMERIC_CHARS:
            raise ValueError("Metric exceeds numeric input budget")
        value = Decimal(source)
    if (
        not value.is_finite()
        or value < 0
        or value.adjusted() >= MAX_INTEGER_DIGITS
        or value.as_tuple().exponent < -MAX_NUMERIC_CHARS
    ):
        raise ValueError("Invalid, negative or oversized metric")
    return value.copy_abs() if value == 0 else value


def _special(raw: object) -> Span | None:
    if raw is None:
        return Span("—")
    if isinstance(raw, Unevaluable):
        return Span(raw.text)
    return None


def percentile(raw: object) -> Span:
    """Module mapping: grade the raw value first, then format to one decimal.

    GOLD asserts source/scope/MAX evidence supplied by the caller. The fixture
    establishes that premise synthetically; this helper never fetches evidence.
    """
    other = _special(raw)
    if other is not None:
        return other
    try:
        value = _number(raw)
        if value > 100:
            raise ValueError("Out-of-range percentile")
        grade = next(g for threshold, g in PERCENTILE_GRADES if value >= threshold)
        with localcontext() as context:
            context.prec = MAX_NUMERIC_CHARS + MAX_INTEGER_DIGITS
            displayed = value.quantize(Decimal(".1"), rounding=ROUND_DOWN)
        return (
            Span(f"{displayed:.1f}%", emphasis="gold")
            if grade == "gold"
            else Span(f"{displayed:.1f}%", color=grade)
        )
    except (ValueError, InvalidOperation):
        return Span("ERR", color="#c83e4d")


def rdps(raw: object) -> Span:
    """Reference presentation policy: integer thousands, ROUND_HALF_UP."""
    other = _special(raw)
    if other is not None:
        return other
    try:
        with localcontext() as context:
            context.prec = MAX_NUMERIC_CHARS + MAX_INTEGER_DIGITS
            value = _number(raw).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return Span(f"{value:,}")
    except (ValueError, InvalidOperation):
        return Span("ERR", color="#c83e4d")


def count(raw: object) -> Span:
    other = _special(raw)
    if other is not None:
        return other
    try:
        value = _number(raw)
        if value != value.to_integral_value():
            raise ValueError("Count must be an integer")
        return Span(str(int(value)))
    except (ValueError, InvalidOperation):
        return Span("ERR", color="#c83e4d")


def _logs_table(records: tuple[Record, ...]) -> Table:
    return Table(
        columns=(
            Column("副本 / 记录版本", weight=3.7),
            Column("职业", weight=1.6),
            Column("最高 rDPS", weight=1.8, align="right", wrap="nowrap"),
            Column("过本次数", weight=1.4, align="right", wrap="nowrap"),
            Column(
                "最佳百分位", weight=1.8, align="right", valign="center", wrap="nowrap"
            ),
        ),
        rows=tuple(
            (
                f"{record.encounter}\n{record.version}",
                record.job,
                Inline((rdps(record.highest_rdps),)),
                Inline((count(record.total_kills),)),
                Inline((percentile(record.best_percentile),)),
            )
            for record in records
        ),
    )


def build_logs_document(theme: str = "cool_white", *, region: str = "CN") -> Document:
    """One long image, with both complete synthetic groups; no fake API query."""
    if region not in ("CN", "Global"):
        raise ValueError("region must be CN or Global")
    domain = "cn.fflogs.com" if region == "CN" else "www.fflogs.com"
    return Document(
        title="ChatGPT @ OpenAI",
        subtitle=f"最终幻想 XIV · FFLogs · {region}",
        brand="游戏连结 · FF14 · Logs",
        logo=meteor_asset(),
        theme=theme,
        layout="table",
        blocks=(
            Section("绝境战", (_logs_table(ULTIMATE),), panel=False),
            Section(
                "零式阿卡狄亚登天斗技场",
                (_logs_table(SAVAGE),),
                subtitle="当前资料片 · 7.x",
                panel=False,
            ),
        ),
        sources=(f"数据源：{domain}",),
    )


def sample_documents() -> dict[str, Document]:
    """Four content specimens, with both light and dark Logs variants."""
    return {
        "01_help_warm_paper": build_help_document(seed=14),
        "02_item_warm_paper": build_item_document(),
        "03_logs_cool_white": build_logs_document("cool_white"),
        "04_logs_deep_indigo": build_logs_document("deep_indigo"),
        "05_market_warm_paper": build_market_document(),
    }


def render_sample(document: Document) -> RenderedBitmap:
    """The complete rendering call; all measurement/painting belongs to Core."""
    return render_document(document)


def render_samples(output_dir: Path) -> dict[str, object]:
    """Write review-only PNGs and provenance outside the repository, exclusively."""
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(ROOT):
        raise ValueError("Review outputs must be outside the repository")
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = sample_documents()
    names = (*documents, "provenance")
    for name in names:
        suffix = ".json" if name == "provenance" else ".png"
        if (output_dir / f"{name}{suffix}").exists():
            raise FileExistsError(output_dir / f"{name}{suffix}")
    results = {}
    for name, document in documents.items():
        rendered = render_sample(document)
        path = output_dir / f"{name}.png"
        with path.open("xb") as stream:
            stream.write(rendered.content)
        results[name] = {
            "file": path.name,
            "width": rendered.width,
            "height": rendered.height,
            "bytes": len(rendered.content),
            "sha256": hashlib.sha256(rendered.content).hexdigest(),
        }
    provenance = {
        "status": "User-approved offline visual fixtures; not live results",
        "rendering": "All PNGs use presentation.layout_renderer.render_document",
        "help": {
            "catalog": "Bundled modules/ff14/yomihime.manifest.json",
            "availability": "Explicit offline loaded/enabled/available fixture only",
        },
        "item": {
            "source": "User-supplied screenshot QQ_1792126562977.png",
            "source_urls": ITEM_SOURCE_URLS,
            "limitations": "Preserves unresolved material IDs 8/13 and class ID 15",
            "design": "User-approved item-detail visual fixture; not a live query",
        },
        "logs": {
            "source": "Synthetic ff14_logs_reference.py layout fixture",
            "boundaries": "Raw 99.96 stays pink; main fixture retains original 99.7",
            "scope": "Seven Ultimate and thirteen Savage rows are not a live catalog",
            "identity": "ChatGPT @ OpenAI is synthetic, not an actual query",
            "rounding": "Percentile ROUND_DOWN; rDPS ROUND_HALF_UP; raw values retained",
        },
        "market": {
            "source": "Historical examples/command_cards/samples/01_market_warm_paper.png",
            "scope": "Synthetic historical illustration; not a live Universalis quote",
            "prices": "NQ 30 Gil/item; HQ 500 Gil/item; China-wide example scope",
            "timestamps": "Relative world upload ages, not individual listing times",
            "limitations": "Finite returned samples, not a complete real-time market scan",
        },
        "images": results,
    }
    with (output_dir / "provenance.json").open("x", encoding="utf-8") as stream:
        json.dump(provenance, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="ygl-declarative-"))
    result = render_samples(output)
    print(
        json.dumps(
            {"output_dir": str(output.resolve()), **result},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
