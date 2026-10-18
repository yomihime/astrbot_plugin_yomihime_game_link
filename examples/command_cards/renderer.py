#!/usr/bin/env python3
"""游戏连结 · warm-paper card design sample, drawn with Python.

No image generation, network requests, or live market API calls.
The SVG renderer accepts only this project's trusted local, monochrome assets;
its validation is a narrow format guard, not an untrusted-SVG security sandbox.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
MAX_WIDTH, MAX_HEIGHT, MAX_PIXELS = 2048, 8192, 8_000_000


@dataclass(frozen=True)
class FontConfig:
    regular: Path
    bold: Path | None = None
    index: int = 0


FONT_CONFIG: FontConfig | None = None


def font_face_index(path: Path) -> int:
    """Prefer the Simplified Chinese face in a Noto CJK collection."""
    if path.suffix.lower() != ".ttc":
        return 0
    for index in range(10):
        try:
            family, _ = ImageFont.truetype(str(path), 16, index=index).getname()
        except OSError:
            break
        if family.endswith(" SC"):
            return index
    return 0


@lru_cache(maxsize=1)
def discover_fonts() -> FontConfig:
    """Discover installed Noto CJK fonts; never download or bundle fonts."""
    roots = [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path.home() / ".fonts",
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
    ]
    if os.name == "nt":
        roots = [
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Microsoft/Windows/Fonts",
        ]
    names = (
        "NotoSansCJKsc-Regular.otf",
        "NotoSansSC-Regular.ttf",
        "NotoSansCJK-Regular.ttc",
    )
    for name in names:
        for root in roots:
            if root.is_dir():
                found = sorted(root.rglob(name))
                if found:
                    regular = found[0]
                    bold = regular.with_name(regular.name.replace("Regular", "Bold"))
                    return FontConfig(
                        regular,
                        bold if bold.is_file() else regular,
                        font_face_index(regular),
                    )
    raise RuntimeError(
        "No Noto CJK font found. Pass --font to an installed Chinese "
        "OTF/TTF/TTC file (optional --bold-font and --font-index)."
    )


def add_font_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--font", type=Path, help="Installed Chinese OTF/TTF/TTC font")
    parser.add_argument(
        "--bold-font", type=Path, help="Optional bold face; regular is reused otherwise"
    )
    parser.add_argument(
        "--font-index", type=int, default=None, help="Optional TTC face index"
    )


def configure_fonts(args: argparse.Namespace) -> None:
    global FONT_CONFIG
    if args.font is not None:
        if not args.font.is_file() or (args.bold_font and not args.bold_font.is_file()):
            raise ValueError("The supplied font file does not exist")
        FONT_CONFIG = FontConfig(
            args.font,
            args.bold_font or args.font,
            args.font_index
            if args.font_index is not None
            else font_face_index(args.font),
        )
    elif args.bold_font is not None or args.font_index is not None:
        base = discover_fonts()
        FONT_CONFIG = FontConfig(
            base.regular,
            args.bold_font or base.bold,
            args.font_index if args.font_index is not None else base.index,
        )


def output_directory(requested: Path | None) -> Path:
    """An omitted path makes a fresh run directory; never overwrite prior runs."""
    if requested is not None:
        requested.mkdir(parents=True, exist_ok=True)
        return requested
    root = Path.cwd() / "command-card-output"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=root))


def save_png(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        image.save(stream, format="PNG", optimize=True)
    return path


SCALE = 2
W, H = 920, 800


@dataclass(frozen=True)
class Palette:
    label: str
    outside: str
    paper: str
    border: str
    rule: str
    panel: str
    tint: str
    text: str
    title: str
    accent: str
    price: str
    secondary: str
    muted: str


# Explicit presets, chosen by configuration. They never change randomly.
PALETTES: dict[str, Palette] = {
    "warm_paper": Palette(
        "暖桃纸",
        "#F3E8DE",
        "#FFF9F1",
        "#EAD7C6",
        "#E9DACD",
        "#FFFDFC",
        "#F6E9DD",
        "#75594B",
        "#5B4033",
        "#B68061",
        "#84573E",
        "#96745D",
        "#AD9380",
    ),
    "soft_lilac": Palette(
        "轻柔紫",
        "#ECE8F3",
        "#FCF9FF",
        "#DFD5EB",
        "#E5DCED",
        "#FFFDFF",
        "#EFE7F6",
        "#635375",
        "#493754",
        "#9476AF",
        "#73528A",
        "#877091",
        "#A08DAA",
    ),
    "cool_mint": Palette(
        "浅薄荷",
        "#E4EEEA",
        "#F7FCF8",
        "#CCDCD3",
        "#D7E3DC",
        "#FEFFFD",
        "#E5F0E9",
        "#4F695B",
        "#344F41",
        "#719B86",
        "#52765F",
        "#718B7B",
        "#90A597",
    ),
}


@dataclass(frozen=True)
class Module:
    key: str
    display_name: str
    icon: Path
    help_command: str
    features: tuple[str, ...]
    loaded: bool = True
    enabled: bool = True
    available: bool = True


FF14 = Module(
    "ff14",
    "FF14",
    ROOT / "assets" / "ff14-meteor.svg",
    "/ygl ff14 help",
    ("物品", "市场", "战绩", "日历"),
)
# Illustrative registry only; an application must supply its actual loaded modules.
MODULES: tuple[Module, ...] = (FF14,)


@dataclass(frozen=True)
class MarketQuote:
    quality: str
    price: int | None
    world: str
    world_upload_age: str


@dataclass(frozen=True)
class MarketData:
    item: str
    item_id: str
    scope: str
    quotes: tuple[MarketQuote, ...]
    module: Module = FF14
    demo: bool = True


DEMO_MARKET = MarketData(
    "犎牛牛排",
    "44091",
    "中国全区",
    (
        MarketQuote("NQ", 30, "静语庄园", "12 分钟前"),
        MarketQuote("HQ", 500, "神拳痕", "2 小时前"),
    ),
)


@dataclass(frozen=True)
class HelpData:
    active_modules: tuple[Module, ...]
    suggested: Module | None
    opener: str


def available_modules(modules: Sequence[Module]) -> tuple[Module, ...]:
    """Select only loaded, enabled, currently available modules, preserving order."""
    result: list[Module] = []
    seen: set[str] = set()
    for module in modules:
        if (
            module.loaded
            and module.enabled
            and module.available
            and module.key not in seen
        ):
            result.append(module)
            seen.add(module.key)
    return tuple(result)


def build_help_data(modules: Sequence[Module], *, seed: int | None = None) -> HelpData:
    """Call once per help request. Reuse the result for every rendering/palette.

    A production request omits seed. A test/demo can pass a seed for repeatability.
    The display name is user-facing; a technical registry key is never the opener.
    """
    active = available_modules(modules)
    selected = random.Random(seed).choice(active) if active else None
    opener = (
        f"想查什么？从 {selected.display_name} 开始吧"
        if selected
        else "暂时没有可用模块，稍后再来看看吧"
    )
    return HelpData(active, selected, opener)


class Canvas:
    def __init__(
        self, bg: str, width: int = W, height: int = H, fonts: FontConfig | None = None
    ):
        if not (
            0 < width <= MAX_WIDTH
            and 0 < height <= MAX_HEIGHT
            and width * height <= MAX_PIXELS
        ):
            raise ValueError("Canvas exceeds prototype image bounds")
        self.w, self.h = width, height
        self.font_config = fonts or FONT_CONFIG or discover_fonts()
        self.drawn_text: list[str] = []
        self.im = Image.new("RGB", (width * SCALE, height * SCALE), bg)
        self.d = ImageDraw.Draw(self.im)
        self.fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}

    def font(self, size: int, bold: bool = False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(
                str(
                    (self.font_config.bold or self.font_config.regular)
                    if bold
                    else self.font_config.regular
                ),
                size * SCALE,
                index=self.font_config.index,
            )
        return self.fonts[key]

    def width(self, text: str, size: int, bold: bool = False) -> float:
        return self.d.textlength(text, font=self.font(size, bold)) / SCALE

    def text(
        self,
        xy,
        text: str,
        size: int = 24,
        fill: str = "#333333",
        bold: bool = False,
        anchor: str = "lt",
    ):
        x, y = xy
        box = self.d.textbbox(
            (x * SCALE, y * SCALE), text, font=self.font(size, bold), anchor=anchor
        )
        if (
            box[0] < 0
            or box[1] < 0
            or box[2] > self.im.width
            or box[3] > self.im.height
        ):
            raise ValueError(f"Text exceeds canvas: {text!r}, bbox={box}")
        self.d.text(
            (x * SCALE, y * SCALE),
            text,
            font=self.font(size, bold),
            fill=fill,
            anchor=anchor,
        )
        self.drawn_text.append(text)

    def fitted(
        self,
        xy,
        text: str,
        max_width: int,
        size: int = 24,
        minimum: int = 18,
        fill: str = "#333333",
        bold: bool = False,
        anchor: str = "lt",
    ):
        size, rendered = fit_text(self, text, max_width, size, minimum, bold)
        self.text(xy, rendered, size, fill, bold, anchor)
        return size, rendered

    def rr(self, box, radius, fill, outline=None, width=1):
        self.d.rounded_rectangle(
            tuple(round(v * SCALE) for v in box),
            radius=round(radius * SCALE),
            fill=fill,
            outline=outline,
            width=width * SCALE,
        )

    def rect(self, box, fill):
        self.d.rectangle(tuple(round(v * SCALE) for v in box), fill=fill)

    def line(self, box, fill, width=1):
        self.d.line(
            tuple(round(v * SCALE) for v in box), fill=fill, width=width * SCALE
        )

    def svg(self, path: Path, xy, size: int, color: str):
        icon = rasterize_svg(path.resolve(), size * SCALE, color)
        self.im.paste(icon, (round(xy[0] * SCALE), round(xy[1] * SCALE)), icon)

    def save(self, path: Path) -> Path:
        return save_png(
            self.im.resize((self.w, self.h), Image.Resampling.LANCZOS), path
        )


def fit_text(
    c: Canvas, text: str, max_width: int, size: int, minimum: int, bold: bool = False
) -> tuple[int, str]:
    """Shrink to a readable minimum, then ellipsize if the reserved box is full."""
    if max_width < c.width("…", minimum, bold):
        raise ValueError("Text box is too narrow for the minimum font size")
    for candidate in range(size, minimum - 1, -1):
        if c.width(text, candidate, bold) <= max_width:
            return candidate, text
    trimmed = text
    while trimmed and c.width(trimmed + "…", minimum, bold) > max_width:
        trimmed = trimmed[:-1]
    return minimum, trimmed + "…"


def phrase_text(
    c: Canvas,
    xy,
    prefix: str,
    name: str,
    suffix: str,
    max_width: int,
    size: int,
    minimum: int,
    fill: str,
    bold: bool = False,
):
    """Keep the helpful sentence ending when an unusually long name is shortened."""
    for candidate in range(size, minimum - 1, -1):
        if c.width(prefix + name + suffix, candidate, bold) <= max_width:
            c.text(xy, prefix + name + suffix, candidate, fill, bold)
            return prefix + name + suffix
    available = max_width - c.width(prefix + suffix, minimum, bold)
    shortened = fit_text(c, name, int(available), minimum, minimum, bold)[1]
    rendered = prefix + shortened + suffix
    c.text(xy, rendered, minimum, fill, bold)
    return rendered


def wrap_text(
    c: Canvas, text: str, max_width: int, size: int, bold: bool = False
) -> list[str]:
    lines, current = [], ""
    for char in text:
        if current and c.width(current + char, size, bold) > max_width:
            lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def item_title(c: Canvas, name: str, p: Palette):
    if c.width(name, 46, True) <= 690:
        c.text((57, 184), name, 46, p.title, True)
        return 248
    for size in range(38, 29, -1):
        lines = wrap_text(c, name, 690, size, True)
        if len(lines) <= 2:
            break
    if len(lines) > 2:
        lines = [lines[0], fit_text(c, "".join(lines[1:]), 690, size, size, True)[1]]
    for i, line in enumerate(lines):
        c.text((57, 178 + i * (size + 5)), line, size, p.title, True)
    return 267


# Strict asset-format guard, deliberately not a general SVG loader or sandbox.
SVG_TAGS = {
    "svg",
    "g",
    "path",
    "circle",
    "ellipse",
    "rect",
    "polygon",
    "polyline",
    "line",
    "title",
    "desc",
}
SVG_ATTRS = {
    "viewBox",
    "width",
    "height",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "fill-rule",
    "clip-rule",
    "d",
    "points",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "transform",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "version",
    "role",
    "aria-labelledby",
    "aria-label",
    "id",
}


def prepare_svg(path: Path, color: str) -> bytes:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError("SVG color must be a six-digit hex color")
    raw = path.read_text(encoding="utf-8")
    if len(raw) > 100_000 or re.search(r"<!DOCTYPE|<!ENTITY|url\s*\(", raw, re.I):
        raise ValueError("Unsupported SVG content in local asset")
    root = ET.fromstring(raw)
    if root.tag.split("}")[-1] != "svg":
        raise ValueError("Expected SVG root")
    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(view_box) != 4 or float(view_box[2]) <= 0 or float(view_box[3]) <= 0:
        raise ValueError("Expected a positive SVG viewBox")
    for element in root.iter():
        if element.tag.split("}")[-1] not in SVG_TAGS:
            raise ValueError(f"Unsupported SVG element: {element.tag}")
        for attr, value in element.attrib.items():
            if attr not in SVG_ATTRS:
                raise ValueError(f"Unsupported SVG attribute: {attr}")
            if attr in {"fill", "stroke"} and value not in {"currentColor", "none"}:
                raise ValueError("Local module SVGs must use currentColor/none only")
    return raw.replace("currentColor", color).encode("utf-8")


@lru_cache(maxsize=64)
def rasterize_svg(path: Path, pixels: int, color: str) -> Image.Image:
    svg = prepare_svg(path, color)
    try:
        import cairosvg
    except ImportError:
        cairosvg = None
    if cairosvg is not None:
        data = cairosvg.svg2png(
            bytestring=svg, output_width=pixels, output_height=pixels, unsafe=False
        )
        return Image.open(BytesIO(data)).convert("RGBA")
    # Preview-only fallback for trusted local assets. No shell interpolation.
    inkscape = shutil.which("inkscape")
    if inkscape is None:
        raise RuntimeError(
            "Install optional CairoSVG or provide Inkscape for trusted local SVG preview"
        )
    with tempfile.TemporaryDirectory(prefix="ygl-svg-") as td:
        source, output = Path(td) / "icon.svg", Path(td) / "icon.png"
        source.write_bytes(svg)
        subprocess.run(
            [
                inkscape,
                str(source),
                "--export-area-page",
                f"--export-width={pixels}",
                f"--export-height={pixels}",
                f"--export-filename={output}",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return Image.open(output).convert("RGBA")


def linkmark(c: Canvas, x: int, y: int, color: str):
    c.rr((x, y + 6, x + 25, y + 28), 7, None, color, 3)
    c.rr((x + 13, y, x + 38, y + 22), 7, None, color, 3)
    c.line((x + 13, y + 15, x + 25, y + 15), color, 3)


def badge(c: Canvas, box, label: str, p: Palette, size: int = 19):
    c.rr(box, (box[3] - box[1]) / 2, p.tint)
    c.text(
        ((box[0] + box[2]) / 2, box[1] + (box[3] - box[1] - size) / 2 - 2),
        label,
        size,
        p.accent,
        True,
        anchor="mt",
    )


def base(p: Palette, header: str, tag: str) -> Canvas:
    c = Canvas(p.outside)
    c.rr((22, 22, 898, 778), 26, p.paper)
    c.rr((35, 35, 885, 765), 21, None, p.border)
    linkmark(c, 57, 53, p.accent)
    c.fitted((109, 57), header, 620, 23, 18, p.text, True)
    if tag:
        badge(c, (751, 49, 863, 86), tag, p)
    c.line((57, 112, 863, 112), p.rule)
    return c


def footer(c: Canvas, p: Palette, kind: str):
    c.line((57, 720, 863, 720), p.rule)
    c.text((57, 739), "如月怜的游戏连结", 19, p.muted)
    c.text((863, 739), f"{p.label} · {kind}", 19, p.muted, anchor="rt")


def render_market(data: MarketData, palette: str = "warm_paper") -> Canvas:
    if len(data.quotes) != 2:
        raise ValueError("This design expects two quality rows (NQ/HQ)")
    p = PALETTES[palette]
    c = base(
        p,
        f"游戏连结 · {data.module.display_name} · 市场",
        "演示数据" if data.demo else "",
    )
    c.text((57, 142), "查到啦", 26, p.accent, True)
    meta_y = item_title(c, data.item, p)
    c.fitted(
        (60, meta_y), f"物品 {data.item_id}  ·  {data.scope}", 690, 24, 20, p.secondary
    )
    c.rr((778, 143, 863, 228), 23, p.tint)
    c.svg(data.module.icon, (783, 148), 75, p.accent)
    for i, row in enumerate(data.quotes):
        top = 302 + i * 144
        c.rr((57, top, 863, top + 128), 20, p.panel, p.border)
        c.rr((73, top + 17, 145, top + 111), 14, p.tint)
        c.fitted((109, top + 37), row.quality, 64, 26, 19, p.accent, True, "mt")
        c.text((109, top + 78), "品质", 18, p.secondary, anchor="mt")
        value = f"{row.price:,}" if row.price is not None else "暂无"
        size, fitted = c.fitted((173, top + 24), value, 218, 69, 30, p.price, True)
        # Keep units on the baseline, with a stable minimum right margin.
        unit_x = 173 + c.width(fitted, size, True) + 13
        if unit_x + c.width("Gil / 件", 22) <= 476:
            c.text((unit_x, top + 64), "Gil / 件", 22, p.secondary)
        else:
            c.text((173, top + 91), "Gil / 件", 18, p.secondary)
        c.line((494, top + 23, 494, top + 105), p.rule)
        c.fitted((523, top + 27), row.world, 316, 27, 20, p.text, True)
        c.fitted(
            (523, top + 74),
            f"世界上传于 {row.world_upload_age}",
            316,
            23,
            18,
            p.secondary,
        )
    c.text((58, 608), "Universalis · 有限返回样本中的最低价", 24, p.text, True)
    c.text((58, 645), "不是全量市场扫描，无法保证此刻仍可买到", 24, p.secondary)
    c.text((58, 678), "时间为对应世界的上传时间，并非该条挂单时间", 24, p.secondary)
    footer(c, p, "市场卡")
    return c


def render_help(data: HelpData, palette: str = "warm_paper") -> Canvas:
    p = PALETTES[palette]
    c = base(p, "游戏连结 · 使用帮助", "总帮助")
    c.text((57, 143), "你好，这里是", 25, p.accent, True)
    c.text((57, 185), "如月怜的游戏连结", 44, p.title, True)
    if data.suggested is None:
        c.fitted((58, 251), data.opener, 806, 25, 19, p.secondary)
    else:
        phrase_text(
            c,
            (58, 251),
            "想查什么？从 ",
            data.suggested.display_name,
            " 开始吧",
            806,
            25,
            19,
            p.secondary,
        )
    c.rr((57, 305, 863, 540), 22, p.panel, p.border)
    module = data.suggested
    if module is None:
        c.text((88, 346), "当前没有可用模块", 32, p.title, True)
        c.text((88, 402), "模块加载完成后，这里会显示可用功能", 24, p.secondary)
        c.text((58, 586), "稍后重新打开帮助即可", 26, p.text, True)
        c.text((58, 639), "还没有可用功能时，不会推荐未加载的模块", 23, p.secondary)
    else:
        c.rr((79, 328, 147, 396), 17, p.tint)
        c.svg(module.icon, (85, 334), 56, p.accent)
        c.fitted((170, 331), module.display_name, 659, 37, 24, p.title, True)
        c.text(
            (171, 381),
            "当前可用模块" if len(data.active_modules) == 1 else "这次推荐的模块",
            21,
            p.secondary,
        )
        c.line((80, 427, 840, 427), p.rule)
        labels = module.features
        if not labels:
            c.text((88, 465), "使用下面的帮助命令查看详情", 25, p.text)
        else:
            col = 760 / len(labels)
            for i, label in enumerate(labels):
                x = 80 + (i + 0.5) * col
                c.fitted((x, 464), label, int(col - 24), 29, 19, p.text, True, "mt")
                if i < len(labels) - 1:
                    rule_x = 80 + (i + 1) * col
                    c.line((rule_x, 466, rule_x, 495), p.rule)
        phrase_text(
            c,
            (58, 574),
            "查看 ",
            module.display_name,
            " 的具体用法",
            806,
            25,
            20,
            p.text,
            True,
        )
        c.rect((58, 616, 63, 664), p.accent)
        c.fitted((82, 620), module.help_command, 757, 33, 22, p.price, True)
        c.text((58, 681), "把这条命令发给我，就能继续查看帮助", 22, p.secondary)
    footer(c, p, "帮助卡")
    return c


def comparison(canvases: Sequence[Canvas], output: Path) -> Path:
    """One common geometry; each column is an explicit palette preset."""
    if len(canvases) != 6:
        raise ValueError("Comparison expects market/help for three palettes")
    thumb_w, thumb_h, gap = 552, 480, 20
    image = Image.new("RGB", (thumb_w * 3 + gap * 4, thumb_h * 2 + gap * 3), "#E9E6E2")
    for i, canvas in enumerate(canvases):
        col, row = i // 2, i % 2
        thumb = canvas.im.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        image.paste(thumb, (gap + col * (thumb_w + gap), gap + row * (thumb_h + gap)))
    return save_png(image, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_font_arguments(parser)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output folder; existing image files are never overwritten",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Deterministic demo/test selection"
    )
    parser.add_argument("--palette", choices=tuple(PALETTES), default="warm_paper")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Also render the three-palette comparison",
    )
    args = parser.parse_args()
    configure_fonts(args)
    output = output_directory(args.output_dir)
    # Exactly one selection per request; changing palette never reselects a module.
    help_data = build_help_data(MODULES, seed=args.seed)
    for name, canvas in (
        (f"01_market_{args.palette}.png", render_market(DEMO_MARKET, args.palette)),
        (f"02_help_{args.palette}.png", render_help(help_data, args.palette)),
    ):
        print(canvas.save(output / name))
    if args.compare:
        canvases = [
            canvas
            for key in PALETTES
            for canvas in (render_market(DEMO_MARKET, key), render_help(help_data, key))
        ]
        print(comparison(canvases, output / "00_palette_comparison.png"))


if __name__ == "__main__":
    main()
