"""Plain text projection for the renderer-neutral display contract.

The presenter deliberately knows only the public display blocks.  It does not
resolve assets, contact a message port, or interpret text as a template.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from yomihime_game_link_sdk.display import (
    CommandsBlock,
    DisplayDocument,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    LinksBlock,
    MetricsBlock,
    MoneyValue,
    NumberValue,
    SeriesBlock,
    TableBlock,
    TextBlock,
    TimeValue,
    UnknownBlock,
)

from ..core.contracts.validation_boundary import validate_contract

_TRUNCATION_NOTICE = "[内容已截断]"


@dataclass(frozen=True)
class _DecimalParts:
    """Exact Decimal coefficient metadata without context arithmetic."""

    sign: bool
    digits: str
    exponent: int


class TextPresenter:
    """Render a :class:`DisplayDocument` as deterministic plain text."""

    def __init__(self) -> None:
        """Create a presenter without binding resources or output services."""

    def render(self, document: DisplayDocument, *, max_chars: int) -> str:
        """Return a bounded text projection of ``document``.

        A budget is intentionally required at every call site.  Truncation is
        performed only after the complete projection has been built, leaving
        the immutable input document untouched.
        """
        validate_contract(document)
        if not isinstance(document, DisplayDocument):
            raise TypeError("document must be a DisplayDocument")
        if not isinstance(max_chars, int) or isinstance(max_chars, bool):
            raise TypeError("max_chars must be a positive integer")
        if max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")

        lines = [document.title, document.subject]
        if document.sources:
            lines.append("来源：" + " / ".join(document.sources))
        rendered_length = sum(map(len, lines)) + len(lines) - 1
        overflow = rendered_length > max_chars
        for block in document.ordered_blocks:
            if overflow:
                break
            block_budget = max(1, max_chars - rendered_length - 1)
            for line in self._render_block(block, block_budget):
                lines.append(line)
                rendered_length += 1 + len(line)
                if rendered_length > max_chars:
                    overflow = True
                    break
        text = "\n".join(lines)
        if not overflow and len(text) <= max_chars:
            return text
        if max_chars < len(_TRUNCATION_NOTICE):
            raise ValueError("max_chars is too small for the truncation notice")
        return text[: max_chars - len(_TRUNCATION_NOTICE)] + _TRUNCATION_NOTICE

    def _render_block(self, block: Any, budget: int) -> list[str]:
        if isinstance(block, TextBlock):
            return [block.text]
        if isinstance(block, FieldsBlock):
            return self._render_mapping_block(block.fields, budget)
        if isinstance(block, MetricsBlock):
            return self._render_mapping_block(block.metrics, budget)
        if isinstance(block, TableBlock):
            header = " | ".join(block.columns)
            lines = [header]
            lines.extend(
                " | ".join(self._format_value(value, budget) for value in row)
                for row in block.rows
            )
            return lines
        if isinstance(block, ItemGridBlock):
            lines = []
            lines.extend(self._render_grid_item(item, budget) for item in block.items)
            if not block.items and block.fallback_text is not None:
                lines.append(block.fallback_text)
            return lines
        if isinstance(block, ImageBlock):
            return [block.alt_text]
        if isinstance(block, SeriesBlock):
            lines = []
            lines.extend(
                f"{self._format_value(when, budget)}: "
                f"{self._format_value(value, budget)}"
                for when, value in block.points
            )
            if not block.points and block.fallback_text is not None:
                lines.append(block.fallback_text)
            return lines
        if isinstance(block, LinksBlock):
            lines = []
            lines.extend(f"{link.label}: {link.url}" for link in block.links)
            if not block.links and block.fallback_text is not None:
                lines.append(block.fallback_text)
            return lines
        if isinstance(block, CommandsBlock):
            lines = []
            lines.extend(block.commands)
            if not block.commands and block.fallback_text is not None:
                lines.append(block.fallback_text)
            return lines
        if isinstance(block, UnknownBlock):
            # Required unknown blocks cannot be constructed by the contract;
            # keep a defensive error if one reaches a presenter through a
            # malformed object created outside the normal protocol.
            if block.required:
                raise ValueError("unknown required display block is unsupported")
            return [block.fallback_text] if block.fallback_text is not None else []
        raise TypeError("document contains an unsupported display block")

    def _render_mapping_block(
        self, values: Mapping[str, Any], budget: int
    ) -> list[str]:
        lines = []
        lines.extend(
            f"{key}: {self._format_value(value, budget)}"
            for key, value in values.items()
        )
        return lines

    def _render_grid_item(self, item: GridItem, budget: int) -> str:
        validate_contract(item)
        return f"{item.label}: {self._format_value(item.value, budget)}"

    def _format_value(self, value: Any, budget: int) -> str:
        if isinstance(value, NumberValue):
            return self._format_number(value.value, value.precision, value.unit, budget)
        if isinstance(value, MoneyValue):
            return self._format_number(
                value.value, value.precision, value.currency, budget
            )
        if isinstance(value, TimeValue):
            return self._format_time(value)
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, Decimal):
            return self._format_number(value, None, "", budget)
        if isinstance(value, (str, int)):
            return str(value)
        if isinstance(value, Mapping):
            entries = (
                f"{key}: {self._format_value(item, budget)}"
                for key, item in value.items()
            )
            return "{" + ", ".join(entries) + "}"
        if isinstance(value, (tuple, list)):
            return (
                "["
                + ", ".join(self._format_value(item, budget) for item in value)
                + "]"
            )
        # Values accepted by the display contract cannot reach this branch.
        # A stable marker is safer than leaking an object's repr or invoking
        # arbitrary conversion code supplied by a malformed caller.
        return "[不支持的值]"

    def _format_number(
        self,
        value: Decimal | int | str,
        precision: int | None,
        suffix: str,
        budget: int,
    ) -> str:
        parts = self._decimal_parts(Decimal(str(value)), precision)
        suffix_length = len(suffix) + (1 if suffix else 0)
        fixed_length = self._fixed_length(parts, precision)
        if fixed_length + suffix_length <= budget:
            result = self._fixed_text(parts, precision)
        else:
            result = self._scientific_text(parts, max(1, budget - suffix_length))
        return f"{result} {suffix}".rstrip() if suffix else result

    def _decimal_parts(self, value: Decimal, precision: int | None) -> _DecimalParts:
        sign, raw_digits, exponent = value.as_tuple()
        parts = _DecimalParts(
            sign=bool(sign), digits="".join(map(str, raw_digits)), exponent=exponent
        )
        if precision is None:
            return parts

        target = -precision
        if parts.exponent >= target:
            return parts
        drop = target - parts.exponent
        digits = parts.digits
        if drop > len(digits):
            return _DecimalParts(parts.sign, "0", target)
        kept = digits[:-drop] if drop else digits
        discarded = digits[-drop:] if drop else ""
        if not kept:
            kept = "0"
        if self._round_up(kept, discarded):
            kept = self._increment_digits(kept)
        return _DecimalParts(parts.sign, kept, target)

    def _round_up(self, kept: str, discarded: str) -> bool:
        if not discarded:
            return False
        first = discarded[0]
        if first > "5":
            return True
        if first < "5":
            return False
        if any(char != "0" for char in discarded[1:]):
            return True
        last = int(kept[-1]) if kept and kept != "0" else 0
        return last % 2 == 1

    def _increment_digits(self, digits: str) -> str:
        result = list(digits)
        for index in range(len(result) - 1, -1, -1):
            if result[index] != "9":
                result[index] = str(int(result[index]) + 1)
                return "".join(result)
            result[index] = "0"
        return "1" + "".join(result)

    def _fixed_length(self, parts: _DecimalParts, precision: int | None) -> int:
        sign_length = int(parts.sign)
        if precision is None:
            if parts.digits == "0" and parts.exponent >= 0:
                return sign_length + 1
            if parts.exponent >= 0:
                return sign_length + len(parts.digits) + parts.exponent
            return sign_length + max(len(parts.digits), -parts.exponent) + 1

        if parts.digits == "0":
            return sign_length + (precision + 2 if precision else 1)
        target = -precision
        digits_length = len(parts.digits) + max(0, parts.exponent - target)
        if precision:
            return sign_length + max(digits_length, precision + 1) + 1
        return sign_length + digits_length

    def _fixed_text(self, parts: _DecimalParts, precision: int | None) -> str:
        sign = "-" if parts.sign else ""
        digits = parts.digits
        if precision is None:
            if digits == "0" and parts.exponent >= 0:
                return sign + "0"
            if parts.exponent >= 0:
                return sign + digits + "0" * parts.exponent
            places = -parts.exponent
            if len(digits) > places:
                return sign + digits[:-places] + "." + digits[-places:]
            return sign + "0." + "0" * (places - len(digits)) + digits

        if digits == "0":
            return sign + ("0." + "0" * precision if precision else "0")
        target = -precision
        if parts.exponent >= target:
            digits += "0" * (parts.exponent - target)
        if not precision:
            return sign + digits
        if len(digits) > precision:
            return sign + digits[:-precision] + "." + digits[-precision:]
        return sign + "0." + "0" * (precision - len(digits)) + digits

    def _scientific_text(self, parts: _DecimalParts, budget: int) -> str:
        sign = "-" if parts.sign else ""
        digits = parts.digits
        exponent = parts.exponent + len(digits) - 1
        exponent_text = f"E{exponent:+d}"
        mantissa_length = len(digits) + (1 if len(digits) > 1 else 0)
        full_length = len(sign) + mantissa_length + len(exponent_text)
        if full_length <= budget:
            mantissa = digits if len(digits) == 1 else digits[0] + "." + digits[1:]
            return sign + mantissa + exponent_text

        prefix = sign + digits[0]
        if len(digits) > 1 and len(prefix) < budget:
            prefix += "."
            available = budget - len(prefix) - len(exponent_text)
            if available > 0:
                prefix += digits[1 : 1 + available]
        if len(prefix) + len(exponent_text) <= budget:
            prefix += exponent_text
        return prefix[:budget]

    def _format_time(self, value: TimeValue) -> str:
        validate_contract(value)
        moment: datetime | date = value.value
        rendered = moment.isoformat()
        if value.timezone_name:
            rendered += f" [{value.timezone_name}]"
        return rendered


__all__ = ["TextPresenter"]
