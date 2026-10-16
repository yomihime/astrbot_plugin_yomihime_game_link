"""Bounded Core admission and lossless storage for public SDK card declarations.

This module does not import a renderer, Pillow, Core business data or the Host.
Card bytes have the privacy of the enclosing DisplayDocument. Decoding assets
and measuring the selected font remain separate presentation boundaries.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from dataclasses import fields
from unicodedata import category

from yomihime_game_link_sdk import cards

_TYPES = {
    name: getattr(cards, name)
    for name in (
        "Asset",
        "Span",
        "Inline",
        "TaggedText",
        "ThemePalette",
        "Text",
        "Section",
        "Row",
        "Property",
        "PropertyGrid",
        "Column",
        "Cell",
        "Table",
        "Image",
        "Document",
    )
}


def _invalid():
    raise ValueError("invalid card declaration")


def validate_card(document: cards.Document) -> cards.Document:
    """Admit the complete immutable graph before copying, storage or painting."""
    total = raw_extra = lines = nodes = rows = sections = asset_count = asset_bytes = 0

    def sequence(value, limit):
        if type(value) is not tuple or len(value) > limit:
            _invalid()

    def choice(value, allowed):
        if type(value) is not str or value not in allowed:
            _invalid()

    def color(value):
        if type(value) is not str or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            _invalid()

    def number(value, low, high):
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not low <= value <= high
        ):
            _invalid()

    def content(value):
        nonlocal total, raw_extra, lines
        if type(value) is cards.TaggedText:
            source = value.source
            if (
                type(source) is not str
                or len(source) > 4096
                or total + raw_extra + len(source) > 24000
            ):
                _invalid()
            from ...presentation.layout_markup import parse_inline_markup

            value = parse_inline_markup(source)
            raw_extra += len(source) - sum(len(span.text) for span in value.spans)
        if type(value) is str:
            text = value
        elif type(value) is cards.Inline:
            sequence(value.spans, 32)
            if not value.spans:
                _invalid()
            for span in value.spans:
                if (
                    type(span) is not cards.Span
                    or type(span.text) is not str
                    or not 0 < len(span.text) <= 512
                ):
                    _invalid()
                choice(
                    span.emphasis, ("normal", "strong", "secondary", "emphasis", "gold")
                )
                if span.color is not None:
                    color(span.color)
                    if span.emphasis == "gold":
                        _invalid()
            text = "".join(span.text for span in value.spans)
        else:
            _invalid()
        if len(text) > 4096 or any(
            c != "\n" and (category(c).startswith("C") or c in "\u2028\u2029")
            for c in text
        ):
            _invalid()
        total += len(text)
        lines += text.count("\n") + 1
        if total + raw_extra > 24000 or lines > 1000:
            _invalid()

    def asset(value):
        nonlocal asset_count, asset_bytes
        if (
            type(value) is not cards.Asset
            or type(value.required) is not bool
            or type(value.alt) is not str
        ):
            _invalid()
        choice(value.kind, ("svg", "png", "jpeg"))
        if len(value.alt) > 512 or any(category(c).startswith("C") for c in value.alt):
            _invalid()
        content(value.alt)
        if type(value.content) not in (str, bytes) or (
            value.kind != "svg" and type(value.content) is not bytes
        ):
            _invalid()
        limit = 131072 if value.kind == "svg" else 1048576
        if not value.content or len(value.content) > limit:
            _invalid()
        size = (
            len(value.content.encode("utf-8"))
            if type(value.content) is str
            else len(value.content)
        )
        if size > limit:
            _invalid()
        asset_count += 1
        asset_bytes += size
        if asset_count > 32 or asset_bytes > 8388608:
            _invalid()

    def align(value):
        choice(value, ("left", "center", "right"))

    def valign(value):
        choice(value, ("top", "center", "bottom"))

    def blocks(values, depth):
        sequence(values, 128)
        for value in values:
            block(value, depth)

    def block(value, depth):
        nonlocal nodes, rows, sections
        nodes += 1
        if nodes > 512 or depth > 8:
            _invalid()
        if type(value) is cards.Text:
            content(value.content)
            choice(value.style, ("title", "heading", "body", "caption", "stat"))
            align(value.align)
            choice(value.wrap, ("wrap", "nowrap"))
        elif type(value) is cards.Section:
            sections += 1
            if sections > 32 or type(value.panel) is not bool:
                _invalid()
            content(value.title)
            content(value.subtitle)
            blocks(value.blocks, depth + 1)
        elif type(value) is cards.Row:
            sequence(value.cells, 8)
            sequence(value.weights, 8)
            if not value.cells or (
                value.weights and len(value.cells) != len(value.weights)
            ):
                _invalid()
            for weight in value.weights:
                number(weight, 0.1, 100)
            valign(value.valign)
            blocks(value.cells, depth + 1)
        elif type(value) is cards.PropertyGrid:
            sequence(value.properties, 64)
            if type(value.columns) is not int or not 1 <= value.columns <= 4:
                _invalid()
            for prop in value.properties:
                if type(prop) is not cards.Property:
                    _invalid()
                content(prop.label)
                content(prop.value)
        elif type(value) is cards.Table:
            sequence(value.columns, 8)
            sequence(value.rows, 200)
            rows += len(value.rows)
            if not value.columns or rows > 200:
                _invalid()
            for column in value.columns:
                if type(column) is not cards.Column:
                    _invalid()
                content(column.title)
                number(column.weight, 0.1, 100)
                align(column.align)
                valign(column.valign)
                choice(column.wrap, ("wrap", "nowrap"))
            for row in value.rows:
                sequence(row, 8)
                if len(row) != len(value.columns):
                    _invalid()
                for cell in row:
                    if type(cell) is cards.Cell:
                        align(cell.align)
                        valign(cell.valign)
                        cell = cell.content
                    content(cell)
        elif type(value) is cards.Image:
            asset(value.asset)
            align(value.align)
            if value.width is not None and (
                type(value.width) is not int or not 16 <= value.width <= 2048
            ):
                _invalid()
            if type(value.height) is not int or not 16 <= value.height <= 1024:
                _invalid()
            content(value.caption)
            choice(value.fit, ("contain", "cover"))
        else:
            _invalid()

    try:
        if type(document) is not cards.Document:
            _invalid()
        if type(document.width) is not int or not 400 <= document.width <= 2048:
            _invalid()
        choice(document.layout, ("directory", "detail", "table"))
        if type(document.theme) is cards.ThemePalette:
            for field in fields(cards.ThemePalette):
                color(getattr(document.theme, field.name))
        else:
            choice(document.theme, ("warm_paper", "cool_white", "deep_indigo"))
        for value in (
            document.title,
            document.subtitle,
            document.brand,
            document.powered_by,
        ):
            content(value)
        sequence(document.sources, 32)
        for source in document.sources:
            content(source)
        if document.logo is not None:
            asset(document.logo)
        blocks(document.blocks, 0)
    except (AttributeError, UnicodeError, OverflowError):
        _invalid()
    return document


def rebuild_card(document: cards.Document | None) -> cards.Document | None:
    """Detach admitted declarations, preserving exact SDK types and immutable bytes."""
    if document is None:
        return None
    validate_card(document)

    def copy(value):
        if type(value) in _TYPES.values():
            return type(value)(
                **{
                    field.name: copy(getattr(value, field.name))
                    for field in fields(value)
                }
            )
        if type(value) is tuple:
            return tuple(copy(item) for item in value)
        return value

    return copy(document)


def encode_card(document: cards.Document) -> dict:
    """Versioned, namespaced representation; raster bytes use bounded base64."""
    validate_card(document)

    def encode(value):
        if type(value) in _TYPES.values():
            return {
                "$card_type": type(value).__name__,
                "fields": {
                    field.name: encode(getattr(value, field.name))
                    for field in fields(value)
                },
            }
        if type(value) is bytes:
            return {"$card_bytes": base64.b64encode(value).decode("ascii")}
        if type(value) is tuple:
            return [encode(item) for item in value]
        return value

    return {"version": 1, "document": encode(document)}


def decode_card(payload: object) -> cards.Document:
    """Reject unknown versions/types/fields rather than losing card information."""
    count = decoded_bytes = 0

    def decode(value, depth=0):
        nonlocal count, decoded_bytes
        count += 1
        if depth > 40 or count > 200000:
            _invalid()
        if type(value) is list:
            if len(value) > 200:
                _invalid()
            return tuple(decode(item, depth + 1) for item in value)
        if type(value) is dict:
            if set(value) == {"$card_bytes"}:
                encoded = value["$card_bytes"]
                if type(encoded) is not str or len(encoded) > 1398104:
                    _invalid()
                decoded_bytes += (len(encoded) // 4) * 3
                if decoded_bytes > 8388672:  # At most 32 assets with two padding bytes.
                    _invalid()
                try:
                    return base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    _invalid()
            if set(value) != {"$card_type", "fields"}:
                _invalid()
            name, values = value["$card_type"], value["fields"]
            if type(name) is not str or name not in _TYPES or type(values) is not dict:
                _invalid()
            kind = _TYPES[name]
            if set(values) != {field.name for field in fields(kind)}:
                _invalid()
            return kind(
                **{key: decode(item, depth + 1) for key, item in values.items()}
            )
        if value is None or type(value) in (str, int, float, bool):
            return value
        _invalid()

    if (
        type(payload) is not dict
        or set(payload) != {"version", "document"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
    ):
        _invalid()
    return validate_card(decode(payload["document"]))
