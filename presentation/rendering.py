"""Bounded generic renderer for frozen display documents and digest batches.

Asset access and image decoding are injected ports. The renderer never turns an
asset identifier into a path or URL, and a PRIVATE audience is presentation
context only; authorization remains the caller's responsibility.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from ..api.display import (
    CommandsBlock,
    DisplayAudience,
    DisplayBatch,
    DisplayDocument,
    DisplayLimits,
    DisplayOutput,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    LinksBlock,
    MetricsBlock,
    MoneyValue,
    NumberValue,
    Privacy,
    SeriesBlock,
    TableBlock,
    TextBlock,
    TimeValue,
    UnknownBlock,
)
from ..api.version import CONTRACT_VERSION

_TRUNCATION_NOTICE = "[内容已截断]"
_TRUNCATION_FALLBACK = "[截断]"


class AssetReader(Protocol):
    """Read an authorized opaque asset reference, enforcing the supplied cap."""

    async def read(
        self, asset_id: str, *, audience: DisplayAudience, max_bytes: int
    ) -> bytes: ...


class ImageBackend(Protocol):
    """Validate/decode image bytes under explicit decompression limits."""

    async def validate(
        self, image_bytes: bytes, *, max_bytes: int, max_dimension: int
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RenderingBounds:
    """Deployment-provided text and resource limits for each rendered page."""

    max_chars_per_page: int
    max_lines_per_page: int
    max_fields_per_block: int
    max_rows_per_block: int
    max_asset_read_bytes: int
    max_image_dimension: int
    max_asset_reads: int
    max_blocks_per_document: int
    max_members_per_batch: int

    def __post_init__(self) -> None:
        for name in (
            "max_chars_per_page",
            "max_lines_per_page",
            "max_fields_per_block",
            "max_rows_per_block",
            "max_asset_read_bytes",
            "max_image_dimension",
            "max_asset_reads",
            "max_blocks_per_document",
            "max_members_per_batch",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


class RenderingFailure(RuntimeError):
    """Controlled rendering failure with no asset or decoder details."""

    code = "display_render_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class GenericDisplayRenderer:
    """Render only generic display DTOs into bounded plain text and asset refs."""

    def __init__(
        self,
        bounds: RenderingBounds,
        *,
        asset_reader: AssetReader | None = None,
        image_backend: ImageBackend | None = None,
    ) -> None:
        if not isinstance(bounds, RenderingBounds):
            raise TypeError("bounds must be RenderingBounds")
        self._bounds = bounds
        self._asset_reader = asset_reader
        self._image_backend = image_backend

    async def render(
        self,
        document: DisplayDocument,
        *,
        limits: DisplayLimits,
        audience: DisplayAudience = DisplayAudience.PUBLIC,
    ) -> DisplayOutput:
        if not isinstance(document, DisplayDocument):
            raise TypeError("document must be a DisplayDocument")
        audience = self._validate_call(limits, audience)
        if audience is DisplayAudience.PUBLIC and document.privacy is Privacy.PRIVATE:
            raise RenderingFailure()
        lines, resources, _ = await self._document_lines(
            document, audience, limits, self._bounds.max_asset_reads
        )
        return self._output(lines, resources, limits.max_pages)

    async def render_batch(
        self, batch: DisplayBatch, limits: DisplayLimits
    ) -> DisplayOutput:
        if not isinstance(batch, DisplayBatch):
            raise TypeError("batch must be a DisplayBatch")
        audience = self._validate_call(limits, batch.audience)
        lines: list[str] = []
        resources: list[tuple[int, str]] = []
        asset_reads = 0
        members = batch.members[: self._bounds.max_members_per_batch]
        for index, item in enumerate(members, start=1):
            if (
                audience is DisplayAudience.PUBLIC
                and item.document.privacy is Privacy.PRIVATE
            ):
                # Defensive for objects reconstructed without dataclass checks.
                raise RenderingFailure()
            if lines:
                lines.append("")
            lines.append(f"摘要成员 {index}")
            line_offset = len(lines)
            member_lines, member_resources, member_reads = await self._document_lines(
                item.document,
                audience,
                limits,
                self._bounds.max_asset_reads - asset_reads,
            )
            lines.extend(member_lines)
            resources.extend(
                (line_offset + line_index, asset_id)
                for line_index, asset_id in member_resources
            )
            asset_reads += member_reads
        if len(batch.members) > len(members):
            lines.append("[摘要成员过多已截断]")
        return self._output(lines, resources, limits.max_pages)

    def _validate_call(
        self, limits: DisplayLimits, audience: DisplayAudience
    ) -> DisplayAudience:
        if not isinstance(limits, DisplayLimits):
            raise TypeError("limits must be DisplayLimits")
        if isinstance(audience, str):
            try:
                audience = DisplayAudience(audience)
            except ValueError:
                raise ValueError("audience must be a DisplayAudience") from None
        if not isinstance(audience, DisplayAudience):
            raise TypeError("audience must be a DisplayAudience")
        return audience

    async def _document_lines(
        self,
        document: DisplayDocument,
        audience: DisplayAudience,
        limits: DisplayLimits,
        max_asset_reads: int,
    ) -> tuple[list[str], list[tuple[int, str]], int]:
        lines = [document.title, document.subject]
        resources: list[tuple[int, str]] = []
        if document.schema_version != CONTRACT_VERSION:
            return [*lines, "[不支持的显示版本]"], [], 0
        if document.sources:
            lines.append("来源：" + " / ".join(document.sources))
        asset_reads = 0
        blocks = document.ordered_blocks[: self._bounds.max_blocks_per_document]
        for block in blocks:
            if isinstance(block, ImageBlock):
                if asset_reads >= max_asset_reads:
                    lines.append(self._fallback(block.fallback_text, block.alt_text))
                    if block.required and block.fallback_text is None:
                        raise RenderingFailure()
                    continue
                if self._visible(block.visibility, document.privacy, audience):
                    asset_reads += 1
                rendered, reference = await self._image_block(
                    block, audience, document.privacy, limits
                )
                if rendered:
                    line_index = len(lines)
                    lines.append(rendered)
                if reference is not None:
                    resources.append((line_index, reference))
                continue
            if isinstance(block, ItemGridBlock):
                remaining_reads = max_asset_reads - asset_reads
                grid_lines, grid_resources, grid_reads = await self._grid_block(
                    block, audience, document.privacy, limits, remaining_reads
                )
                line_offset = len(lines)
                lines.extend(grid_lines)
                resources.extend(
                    (line_offset + line_index, asset_id)
                    for line_index, asset_id in grid_resources
                )
                asset_reads += grid_reads
                continue
            try:
                lines.extend(self._render_block(block))
            except Exception:
                fallback = getattr(block, "fallback_text", None)
                if fallback is not None:
                    lines.append(fallback)
                elif getattr(block, "required", False):
                    raise RenderingFailure() from None
                # Optional blocks without a fallback are safely omitted.
        if len(document.ordered_blocks) > len(blocks):
            lines.append(_TRUNCATION_NOTICE)
        return lines, resources, asset_reads

    async def _image_block(
        self,
        block: ImageBlock,
        audience: DisplayAudience,
        document_privacy: Privacy,
        limits: DisplayLimits,
    ) -> tuple[str | None, str | None]:
        if not self._visible(block.visibility, document_privacy, audience):
            if block.required:
                return self._fallback(block.fallback_text, block.alt_text), None
            return None, None
        try:
            await self._check_asset(block.asset_id, audience, limits)
        except Exception:
            if block.required and block.fallback_text is None:
                raise RenderingFailure() from None
            return self._fallback(block.fallback_text, block.alt_text), None
        return block.alt_text, block.asset_id

    async def _grid_block(
        self,
        block: ItemGridBlock,
        audience: DisplayAudience,
        document_privacy: Privacy,
        limits: DisplayLimits,
        remaining_asset_reads: int,
    ) -> tuple[list[str], list[tuple[int, str]], int]:
        lines = []
        resources: list[tuple[int, str]] = []
        reads = 0
        seen = 0
        for item in block.items:
            if not self._visible(item.visibility, document_privacy, audience):
                continue
            if seen >= self._bounds.max_fields_per_block:
                if lines:
                    lines[-1] += " " + _TRUNCATION_NOTICE
                else:
                    lines.append(_TRUNCATION_NOTICE)
                break
            seen += 1
            resource_id = None
            if item.asset_id is not None:
                if reads >= remaining_asset_reads:
                    if block.required:
                        if block.fallback_text is None:
                            raise RenderingFailure()
                        return [block.fallback_text], [], reads
                    continue
                reads += 1
                try:
                    await self._check_asset(item.asset_id, audience, limits)
                except Exception:
                    if block.required:
                        if block.fallback_text is None:
                            raise RenderingFailure() from None
                        return [block.fallback_text], [], reads
                    continue
                resource_id = item.asset_id
            line_index = len(lines)
            lines.append(self._render_grid_item(item))
            if resource_id is not None:
                resources.append((line_index, resource_id))
        if not lines:
            if block.fallback_text is not None:
                lines.append(block.fallback_text)
            elif block.required:
                raise RenderingFailure()
            else:
                return [], [], reads
        return lines, resources, reads

    async def _check_asset(
        self, asset_id: str, audience: DisplayAudience, limits: DisplayLimits
    ) -> None:
        if self._asset_reader is None or self._image_backend is None:
            raise RenderingFailure()
        max_bytes = min(limits.max_image_bytes, self._bounds.max_asset_read_bytes)
        content = await self._asset_reader.read(
            asset_id, audience=audience, max_bytes=max_bytes
        )
        if not isinstance(content, bytes) or len(content) > max_bytes:
            raise RenderingFailure()
        result = self._image_backend.validate(
            content,
            max_bytes=max_bytes,
            max_dimension=self._bounds.max_image_dimension,
        )
        if inspect.isawaitable(result):
            await result

    def _visible(
        self, visibility: Privacy, document_privacy: Privacy, audience: DisplayAudience
    ) -> bool:
        if visibility is Privacy.PRIVATE:
            return audience is DisplayAudience.PRIVATE
        return audience is DisplayAudience.PRIVATE or document_privacy is Privacy.PUBLIC

    def _render_block(self, block: Any) -> list[str]:
        if isinstance(block, TextBlock):
            return [block.text]
        if isinstance(block, FieldsBlock):
            return self._mapping_block(block.fields)
        if isinstance(block, MetricsBlock):
            return self._mapping_block(block.metrics)
        if isinstance(block, TableBlock):
            columns = block.columns[: self._bounds.max_fields_per_block]
            lines = [" | ".join(columns)]
            for row in block.rows[: self._bounds.max_rows_per_block]:
                lines.append(
                    " | ".join(
                        self._format_value(value) for value in row[: len(columns)]
                    )
                )
            if not block.rows and block.fallback_text:
                lines.append(block.fallback_text)
            if (
                len(block.columns) > len(columns)
                or len(block.rows) > self._bounds.max_rows_per_block
            ):
                lines[-1] += " " + _TRUNCATION_NOTICE
            return lines
        if isinstance(block, SeriesBlock):
            if not block.points and block.required:
                if block.fallback_text is None:
                    raise RenderingFailure()
                return [block.fallback_text]
            lines = []
            lines.extend(
                f"{self._format_value(when)}: {self._format_value(value)}"
                for when, value in block.points[: self._bounds.max_rows_per_block]
            )
            if not block.points and block.fallback_text:
                lines.append(block.fallback_text)
            if len(block.points) > self._bounds.max_rows_per_block:
                lines[-1] += " " + _TRUNCATION_NOTICE
            return lines
        if isinstance(block, LinksBlock):
            if not block.links and block.required:
                if block.fallback_text is None:
                    raise RenderingFailure()
                return [block.fallback_text]
            lines = []
            lines.extend(
                f"{link.label}: {link.url}"
                for link in block.links[: self._bounds.max_rows_per_block]
            )
            if not block.links and block.fallback_text:
                lines.append(block.fallback_text)
            if len(block.links) > self._bounds.max_rows_per_block:
                lines[-1] += " " + _TRUNCATION_NOTICE
            return lines
        if isinstance(block, CommandsBlock):
            if not block.commands and block.required:
                if block.fallback_text is None:
                    raise RenderingFailure()
                return [block.fallback_text]
            lines = []
            lines.extend(block.commands[: self._bounds.max_rows_per_block])
            if not block.commands and block.fallback_text:
                lines.append(block.fallback_text)
            if len(block.commands) > self._bounds.max_rows_per_block:
                lines[-1] += " " + _TRUNCATION_NOTICE
            return lines
        if isinstance(block, UnknownBlock):
            if block.required:
                if block.fallback_text is None:
                    raise RenderingFailure()
                return [block.fallback_text]
            return [block.fallback_text] if block.fallback_text is not None else []
        raise RenderingFailure()

    def _mapping_block(self, values: Mapping[str, Any]) -> list[str]:
        entries = list(values.items())[: self._bounds.max_fields_per_block]
        lines = []
        lines.extend(f"{key}: {self._format_value(value)}" for key, value in entries)
        if len(values) > len(entries):
            lines[-1] += " " + _TRUNCATION_NOTICE
        return lines

    def _render_grid_item(self, item: GridItem) -> str:
        return f"{item.label}: {self._format_value(item.value)}"

    def _format_value(self, value: Any) -> str:
        if isinstance(value, NumberValue):
            return self._format_number(value.value, value.precision, value.unit)
        if isinstance(value, MoneyValue):
            return self._format_number(value.value, value.precision, value.currency)
        if isinstance(value, TimeValue):
            return self._format_time(value)
        if isinstance(value, Decimal):
            return self._format_number(value, None, "")
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (str, int)):
            return str(value)
        if isinstance(value, Mapping):
            pairs = list(value.items())[: self._bounds.max_fields_per_block]
            rendered = [f"{key}: {self._format_value(item)}" for key, item in pairs]
            if len(value) > len(pairs):
                rendered.append(_TRUNCATION_NOTICE)
            return "{" + ", ".join(rendered) + "}"
        if isinstance(value, (tuple, list)):
            values = value[: self._bounds.max_fields_per_block]
            rendered = [self._format_value(item) for item in values]
            if len(value) > len(values):
                rendered.append(_TRUNCATION_NOTICE)
            return "[" + ", ".join(rendered) + "]"
        return "[不支持的值]"

    def _format_number(
        self, value: Decimal | int | str, precision: int | None, suffix: str
    ) -> str:
        # Decimal formatting is bounded before conversion, including extreme exponents.
        number = Decimal(str(value))
        if not number:
            rendered = "0"
        elif abs(number.adjusted()) > self._bounds.max_chars_per_page:
            rendered = format(number, ".6E")
        elif precision is not None and precision > self._bounds.max_chars_per_page:
            rendered = format(number, ".6E")
        else:
            rendered = (
                format(number, f".{precision}f")
                if precision is not None
                else str(number)
            )
        rendered = self._clip(rendered, self._bounds.max_chars_per_page)
        suffix = self._clip(suffix, self._bounds.max_chars_per_page // 3)
        return f"{rendered} {suffix}".rstrip() if suffix else rendered

    def _format_time(self, value: TimeValue) -> str:
        moment: datetime | date = value.value
        rendered = moment.isoformat()
        if value.timezone_name:
            rendered += f" [{value.timezone_name}]"
        return rendered

    def _output(
        self,
        lines: list[str],
        resources: list[tuple[int, str]],
        max_pages: int,
    ) -> DisplayOutput:
        total_chars = self._bounds.max_chars_per_page * max_pages
        total_lines = self._bounds.max_lines_per_page * max_pages
        bounded: list[tuple[int | None, str]] = []
        truncated = len(lines) > total_lines
        for index, line in enumerate(lines[:total_lines]):
            line = self._single_line(line)
            used_chars = sum(len(part) for _, part in bounded) + max(
                0, len(bounded) - 1
            )
            remaining = total_chars - used_chars - (1 if bounded else 0)
            if remaining <= 0:
                truncated = True
                break
            clipped = self._clip(line, remaining)
            bounded.append((index if clipped == line else None, clipped))
            if clipped != line:
                truncated = True
                break
        if truncated:
            marker = self._truncation_marker(total_chars)
            while bounded:
                used_chars = sum(len(part) for _, part in bounded) + max(
                    0, len(bounded) - 1
                )
                marker_separator = 1 if bounded else 0
                if (
                    len(bounded) < total_lines
                    and used_chars + marker_separator + len(marker) <= total_chars
                ):
                    break
                bounded.pop()
            bounded.append((None, marker))
        text = "\n".join(line for _, line in bounded)
        if not text:
            text = "[无可显示内容]"
        kept_indexes = {index for index, _ in bounded if index is not None}
        unique_resources = tuple(
            dict.fromkeys(
                asset_id
                for line_index, asset_id in resources
                if line_index in kept_indexes
            )
        )
        return DisplayOutput(text, unique_resources)

    def _clip(self, value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        return value[: max(0, max_chars - 1)] + "…"

    def _truncation_marker(self, max_chars: int) -> str:
        if len(_TRUNCATION_NOTICE) <= max_chars:
            return _TRUNCATION_NOTICE
        if len(_TRUNCATION_FALLBACK) <= max_chars:
            return _TRUNCATION_FALLBACK
        return "…"

    def _single_line(self, value: str) -> str:
        return "".join(
            " "
            if character in "\r\n\t\v\f\u2028\u2029" or ord(character) < 32
            else character
            for character in value
        )

    def _fallback(self, fallback: str | None, alt_text: str) -> str:
        return fallback if fallback is not None else f"图片不可用: {alt_text}"


__all__ = [
    "AssetReader",
    "GenericDisplayRenderer",
    "ImageBackend",
    "RenderingBounds",
    "RenderingFailure",
]
