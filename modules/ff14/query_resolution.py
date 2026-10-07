"""Deterministic FF14 query contract, with no market HTTP or entry registration.

Catalogs are injected read-only values. S1 supplies the real catalog; this module
contains no embedded World/DC directory. P1 must supply a trusted owner before
using stateful item selection. Plain request JSON is never an owner authority.
"""

from __future__ import annotations

import re
import secrets
import shlex
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from yomihime_sdk.api.results import ErrorCode
from yomihime_sdk.api.services import ModuleServices

from .features.item_resolution import (
    ItemSearch,
    has_control,
    item_id_from_query,
    search_item_candidates,
    validate_item_query,
)
from .models import ItemCandidate

PARSER_VERSION = "ff14-market-query-v1"
GLOBAL_REGIONS = ("North-America", "Europe", "Japan", "Oceania")
SUPPORTED_REGIONS = ("China", *GLOBAL_REGIONS)
_KEYS = frozenset(("query", "server", "dc", "region", "quality", "intent"))


class QueryResolutionError(ValueError):
    """Stable SDK error category; choices are typed catalog values, not prose."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.PARAMETER_ERROR,
        *,
        choices: tuple[CatalogEntry, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.choices = choices


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    kind: str
    id: int | str
    name: str
    region: str
    dc_id: int | str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        numeric_id = type(self.id) is int and self.id > 0
        named_dc = self.kind == "dc" and type(self.id) is str and self.id == self.name
        if self.kind not in ("world", "dc") or not (numeric_id or named_dc):
            raise ValueError("catalog kind and ID are invalid")
        _text(self.name, "catalog name")
        _text(self.region, "catalog region")
        if self.kind == "world":
            numeric_parent = type(self.dc_id) is int and self.dc_id > 0
            named_parent = type(self.dc_id) is str and bool(_text(self.dc_id, "DC ID"))
            if not (numeric_parent or named_parent):
                raise ValueError("World requires a DC ID")
        elif self.dc_id is not None:
            raise ValueError("DC may not have a parent DC")
        if type(self.aliases) is not tuple:
            raise ValueError("aliases must be an immutable tuple")
        for alias in self.aliases:
            _text(alias, "catalog alias")


@dataclass(frozen=True, slots=True)
class ScopeCatalog:
    entries: tuple[CatalogEntry, ...]
    available: bool = True

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or type(self.available) is not bool:
            raise ValueError("catalog must be immutable")
        if any(not isinstance(entry, CatalogEntry) for entry in self.entries):
            raise ValueError("invalid catalog entry")
        identities = tuple((entry.kind, entry.id) for entry in self.entries)
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate catalog IDs")
        dcs = {entry.id: entry for entry in self.entries if entry.kind == "dc"}
        for entry in self.entries:
            if entry.kind == "world":
                parent = dcs.get(entry.dc_id)
                if parent is None or parent.region != entry.region:
                    raise ValueError("World catalog parent/region mismatch")

    def find(self, kind: str, value: object) -> CatalogEntry:
        if not self.available:
            raise QueryResolutionError("范围目录暂不可用。", ErrorCode.UPSTREAM_ERROR)
        token = _scope_token(value, kind)
        candidates = tuple(
            entry
            for entry in self.entries
            if entry.kind == kind
            and (
                str(entry.id) == token
                or entry.name.casefold() == token.casefold()
                or any(alias.casefold() == token.casefold() for alias in entry.aliases)
            )
        )
        if not candidates:
            raise QueryResolutionError("目录中没有该范围；请核对名称或 ID。")
        if len(candidates) > 1:
            raise QueryResolutionError(
                "范围名称有歧义，请选择明确 ID。", choices=candidates
            )
        entry = candidates[0]
        if entry.region not in SUPPORTED_REGIONS:
            raise QueryResolutionError("该区域尚未支持。", ErrorCode.UNSUPPORTED)
        return entry


@dataclass(frozen=True, slots=True)
class QueryDefaults:
    default_region: str
    core_revision: int
    module_revision: int

    def __post_init__(self) -> None:
        if self.default_region not in ("cn", "global"):
            raise QueryResolutionError("Core 默认区域无效。")
        for value in (self.core_revision, self.module_revision):
            if type(value) is not int or value < 0:
                raise QueryResolutionError("配置 revision 无效。")

    @classmethod
    async def capture(cls, services: ModuleServices) -> QueryDefaults:
        # C1 already captures both authorities under its mutation gate. Read it
        # once; do not fetch Core independently or read the obsolete Host values.
        snapshot = await services.config.current()
        core = snapshot.values.get("core_defaults")
        if not isinstance(core, Mapping):
            raise QueryResolutionError(
                "Core 默认配置不可用。", ErrorCode.MODULE_UNAVAILABLE
            )
        return cls(core.get("default_region"), core.get("revision"), snapshot.revision)


@dataclass(frozen=True, slots=True)
class QueryScope:
    kind: str
    regions: tuple[str, ...]
    target: str | int | None
    source: str


@dataclass(frozen=True, slots=True)
class MarketQuery:
    query: str
    item_id: int | None
    scope: QueryScope
    quality: str
    intent: str
    core_revision: int
    module_revision: int
    parser_version: str = PARSER_VERSION
    original_query: str | None = None
    item_name: str | None = None


@dataclass(frozen=True, slots=True)
class RequestPlan:
    endpoint: str
    scopes: tuple[str | int, ...]
    item_id: int
    parameters: tuple[tuple[str, str], ...]


def request_plan(query: MarketQuery) -> RequestPlan:
    """Bounded API parameters only; never submits HTTP or fabricates global."""
    if query.item_id is None:
        raise QueryResolutionError("请先选择物品。")
    scopes = (
        (query.scope.target,) if query.scope.target is not None else query.scope.regions
    )
    parameters: tuple[tuple[str, str], ...] = ()
    endpoint = "aggregated"
    if query.intent == "listings":
        endpoint = "currently-shown"
        parameters = (("listings", "6"), ("entries", "0"))
        if query.quality != "all":
            parameters += (("hq", "true" if query.quality == "hq" else "false"),)
    return RequestPlan(endpoint, scopes, query.item_id, parameters)


def _text(value: object, field: str, limit: int = 120) -> str:
    if not isinstance(value, str) or has_control(value):
        raise QueryResolutionError(f"{field} 必须为有效文本。")
    value = value.strip()
    if not value or len(value) > limit:
        raise QueryResolutionError(f"{field} 长度无效。")
    return value


def _scope_token(value: object, field: str) -> str:
    if type(value) is int:
        if not 1 <= value <= 2_147_483_647:
            raise QueryResolutionError(f"{field} ID 无效。")
        return str(value)
    return _text(value, field)


def _region(value: object) -> tuple[str, ...]:
    value = _text(value, "region")
    if value == "cn":
        return ("China",)
    if value == "global":
        return GLOBAL_REGIONS
    if value in SUPPORTED_REGIONS:
        return (value,)
    raise QueryResolutionError("区域不受支持；请使用 cn/global 或标准区域。")


class MarketQueryResolver:
    def __init__(self, catalog: ScopeCatalog) -> None:
        self.catalog = catalog

    def parse(
        self, parameters: Mapping[str, object], defaults: QueryDefaults
    ) -> MarketQuery:
        if not isinstance(parameters, Mapping) or any(
            key not in _KEYS for key in parameters
        ):
            raise QueryResolutionError("查询包含未知参数。")
        try:
            query = validate_item_query(parameters.get("query"))
        except ValueError as exc:
            raise QueryResolutionError(str(exc)) from exc
        quality = parameters.get("quality", "all")
        intent = parameters.get("intent", "overview")
        if type(quality) is not str or quality not in ("all", "nq", "hq"):
            raise QueryResolutionError("quality 必须为 all/nq/hq。")
        if type(intent) is not str or intent not in ("overview", "min", "listings"):
            raise QueryResolutionError("intent 必须为 overview/min/listings。")
        world = (
            self.catalog.find("world", parameters["server"])
            if "server" in parameters
            else None
        )
        dc = self.catalog.find("dc", parameters["dc"]) if "dc" in parameters else None
        regions = _region(parameters["region"]) if "region" in parameters else None
        if world and dc and world.dc_id != dc.id:
            raise QueryResolutionError("World 与大区范围冲突。")
        narrow = world or dc
        if narrow and regions and narrow.region not in regions:
            raise QueryResolutionError("服务器/大区与区域冲突。")
        if narrow:
            scope = QueryScope(narrow.kind, (narrow.region,), narrow.id, "explicit")
        elif regions:
            scope = QueryScope("region", regions, None, "explicit")
        else:
            scope = QueryScope(
                "region", _region(defaults.default_region), None, "default"
            )
        return MarketQuery(
            query,
            item_id_from_query(query),
            scope,
            quality,
            intent,
            defaults.core_revision,
            defaults.module_revision,
        )


def structured_parameters(parameters: Mapping[str, object]) -> dict[str, object]:
    """Page and future tool adapters share this literal structured contract."""
    if not isinstance(parameters, Mapping):
        raise QueryResolutionError("请提供结构化查询参数。")
    return dict(parameters)


def command_parameters(text: str) -> dict[str, object]:
    """Explicit key=value boundaries; quotes preserve spaces in item/scope names.

    Accepts the command argument tail only, not a registered /ygl capability.
    Unkeyed tokens are item text; ambiguous scope/quality tokens are rejected.
    """
    text = _text(text, "command", 512)
    # Preserve whether a whole token was quoted so "HQ" and "region=cn Ore"
    # can be literal item names. Escapes are unsupported by the source contract.
    pattern = re.compile(r"""(?:[^\s"'\\]+|"[^"\\]*"|'[^'\\]*')+""")
    tokens: list[tuple[str, bool]] = []
    end = 0
    for match in pattern.finditer(text):
        if text[end : match.start()].strip():
            raise QueryResolutionError("命令引号不完整或包含不支持的转义。")
        raw = match.group()
        cooked = shlex.split(raw, posix=True)[0]
        tokens.append((cooked, raw.startswith(('"', "'"))))
        end = match.end()
    if text[end:].strip():
        raise QueryResolutionError("命令引号不完整或包含不支持的转义。")
    result: dict[str, object] = {}
    item_tokens: list[str] = []
    for token, quoted in tokens:
        if "=" in token and not quoted:
            key, value = token.split("=", 1)
            if key not in _KEYS or key in result or not value:
                raise QueryResolutionError("未知、重复或空命令参数。")
            result[key] = value
        else:
            # Multiword quoted literals stay intact. Standalone syntax words
            # should never silently become an item or apply an implicit default.
            if not quoted and token.casefold() in (
                "hq",
                "nq",
                "all",
                "cn",
                "global",
                "国服",
                "国际服",
            ):
                raise QueryResolutionError("请用 key=value 明确范围或品质。")
            item_tokens.append(token)
    if item_tokens:
        if "query" in result:
            raise QueryResolutionError("物品参数重复。")
        result["query"] = " ".join(item_tokens)
    return result


def natural_parameters(text: str) -> dict[str, object]:
    """Only three explicit grammars, with no listener, inference or LLM calls."""
    text = _text(text, "natural", 512)
    if text.endswith(("？", "?")):
        text = text[:-1]
    # Quote boundaries precede grammar matching: quoted World/DC names may
    # themselves contain 的 or 大区, and quoted item names remain literal.
    _outside_separators(text, "")
    if text.startswith("查一下") and text.endswith("多少钱"):
        scope, item = _natural_split(text[len("查一下") : -len("多少钱")], "的")
        return {"query": _natural_item(item), "server": _natural_item(scope)}
    if text.endswith("什么价"):
        scope, tail = _natural_split(text[: -len("什么价")].strip(), "大区")
        match = re.fullmatch(r"\s*(.+?)\s+(HQ|NQ|all)\s*", tail)
        if match is None:
            raise QueryResolutionError("请明确物品名称与 HQ/NQ/all 品质。")
        item, quality = match.groups()
        return {
            "query": _natural_item(item),
            "dc": _natural_item(scope),
            "quality": quality.lower(),
        }
    if text.endswith("国服哪里最便宜"):
        return {
            "query": _natural_item(text[: -len("国服哪里最便宜")]),
            "region": "cn",
            "intent": "min",
        }
    raise QueryResolutionError("语义边界不明确，请使用物品名称和 key=value 范围。")


def _outside_separators(text: str, separator: str) -> tuple[int, ...]:
    quote: str | None = None
    positions = []
    for index, character in enumerate(text):
        if character == "\\":
            raise QueryResolutionError("自然语法不支持转义，请使用结构化参数。")
        if quote is not None:
            if character == quote:
                quote = None
        elif character in ('"', "'"):
            quote = character
        elif separator and text.startswith(separator, index):
            positions.append(index)
    if quote is not None:
        raise QueryResolutionError("引号不完整，请明确名称边界。")
    return tuple(positions)


def _natural_split(text: str, separator: str) -> tuple[str, str]:
    positions = _outside_separators(text, separator)
    if len(positions) != 1:
        raise QueryResolutionError("名称边界有歧义，请用引号保护名称。")
    position = positions[0]
    return text[:position], text[position + len(separator) :]


def _natural_item(value: str) -> str:
    value = value.strip()
    if value.startswith(('"', "'")):
        if re.fullmatch(r"""(?:"[^"\\]*"|'[^'\\]*')""", value) is None:
            raise QueryResolutionError("请完整引用名称，不要在引号外追加词语。")
        try:
            tokens = shlex.split(value)
        except ValueError as exc:
            raise QueryResolutionError("物品引号不完整。") from exc
        if len(tokens) != 1:
            raise QueryResolutionError("请明确物品名称边界。")
        return tokens[0]
    if '"' in value or "'" in value or "=" in value:
        raise QueryResolutionError("请明确物品名称边界。")
    # These words in an unquoted item position could be a misparsed modifier.
    if re.search(r"(?:HQ|NQ|\bquality\b|\bregion\b|\bintent\b)", value, re.I):
        raise QueryResolutionError("请用引号保护名称，或使用显式参数。")
    return value


@dataclass(frozen=True, slots=True)
class TrustedOwner:
    """Adapter-supplied identity, never deserialized from ordinary query JSON.

    The caller must obtain all three components from its trusted invocation
    context. Construction is not authentication; anonymous callers cannot use
    this stateful API. Formal page identity binding remains P1 work.
    """

    user: str
    session: str
    origin: str

    def __post_init__(self) -> None:
        for value in (self.user, self.session, self.origin):
            _text(value, "trusted owner", 256)


@dataclass(frozen=True, slots=True)
class QueryTicket:
    owner: TrustedOwner
    generation: str


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    batch_id: str
    generation: str
    query: MarketQuery
    candidates: tuple[ItemCandidate, ...]
    truncated: bool


@dataclass(slots=True)
class _Pending:
    generation: str
    expires: float
    batch: CandidateBatch | None = None
    source_event: object | None = None
    consumed_batch: CandidateBatch | None = None
    confirmation_event: object | None = None


def _confirmation_text(value: str) -> str:
    return " ".join(value.casefold().split()).rstrip("。.!！")


def _closed_reference(text: str) -> bool:
    """Whole-message position/denial syntax, independent of capacity."""
    return bool(
        re.fullmatch(
            r"第\s*[+-]?(?:[0-9０-９]+(?:[.．][0-9０-９]+)?|[零〇一二三四五六七八九十百千万亿两]+)\s*[个個项項条條]?[？?]?",
            text,
        )
        or text.rstrip("？?")
        in {
            "最后一个",
            "最后那个",
            "前一个",
            "后一个",
            "前者",
            "后者",
            "前面那个",
            "后面那个",
            "上一个",
            "下一个",
            "第几个",
            "刚才那个",
            "就那个",
            "那个",
            "这个",
            "这一个",
            "那一个",
            "不是",
            "不要",
            "不选",
        }
    )


def _continuation_message(value: str, query: str) -> bool:
    text = _confirmation_text(value)
    query_text = _confirmation_text(query)
    return (
        _closed_reference(text)
        or _closed_reference(query_text)
        or bool(re.fullmatch(r"(?:不要|不是|不选).*", query_text))
        or bool(
            re.fullmatch(
                r"选择物品\s+[0-9]+|就选.+|就.+|选择.+|选.+|(?:不要|不是|不选).*|.+那个[？?]?",
                text,
            )
        )
    )


class CandidateRegistry:
    """Initial ephemeral policy: TTL 5 min, 128 owners, at most 6 candidates.

    No persistence, timers, or runtime acceptance claims. The clock is injected
    in tests. One latest generation per trusted owner; random generations avoid
    keeping an unbounded per-user counter/tombstone collection after eviction.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 300,
        capacity: int = 128,
        max_candidates: int = 6,
    ) -> None:
        if type(ttl) not in (int, float) or not 0 < ttl <= 300:
            raise ValueError("ttl must be in (0, 300]")
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("capacity must be in [1, 128]")
        if type(max_candidates) is not int or not 1 <= max_candidates <= 6:
            raise ValueError("candidate bound must be in [1, 6]")
        self._clock, self._ttl = clock, ttl
        self._capacity, self._max_candidates = capacity, max_candidates
        self._pending: OrderedDict[TrustedOwner, _Pending] = OrderedDict()

    def _prune(self) -> None:
        now = self._clock()
        for owner in tuple(self._pending):
            if self._pending[owner].expires <= now:
                del self._pending[owner]

    @property
    def size(self) -> int:
        self._prune()
        return len(self._pending)

    def begin(
        self, owner: TrustedOwner | None, *, source_event: object | None = None
    ) -> QueryTicket:
        if not isinstance(owner, TrustedOwner):
            raise QueryResolutionError(
                "缺少可信用户/会话绑定，无法续接候选。", ErrorCode.UNSUPPORTED
            )
        self._prune()
        self._pending.pop(owner, None)
        while len(self._pending) >= self._capacity:
            self._pending.popitem(last=False)
        ticket = QueryTicket(owner, secrets.token_urlsafe(18))
        self._pending[owner] = _Pending(
            ticket.generation, self._clock() + self._ttl, source_event=source_event
        )
        return ticket

    def tool_query(
        self, owner: TrustedOwner, event: object, text: str, query: str
    ) -> CandidateBatch | None:
        """Keep candidate continuations out of the new-query invalidation path."""
        self._prune()
        pending = self._pending.get(owner)
        if pending is None or (
            pending.batch is None and pending.consumed_batch is None
        ):
            if _continuation_message(text, query):
                raise QueryResolutionError(
                    "没有有效候选可供确认，请先明确提出新的物品查询。"
                )
            return None
        batch = pending.batch or pending.consumed_batch
        name, message = _confirmation_text(query), _confirmation_text(text)
        original = _confirmation_text(batch.query.query)
        if (
            pending.batch is not None
            and event is pending.source_event
            and name == original
        ):
            return batch  # Retry keeps the complete frozen context and ticket.
        candidate_names = tuple(
            _confirmation_text(item.name) for item in batch.candidates
        )
        candidate_ids = tuple(str(item.item_id) for item in batch.candidates)
        if (
            event is pending.source_event
            or event is pending.confirmation_event
            or _continuation_message(text, query)
            or name in candidate_names
            or name in candidate_ids
            or (
                name != original
                and bool(name)
                and any(name in candidate for candidate in candidate_names)
            )
            or any(candidate and candidate in message for candidate in candidate_names)
            or any(
                re.search(r"(?<![0-9])" + candidate + r"(?![0-9])", message)
                for candidate in candidate_ids
            )
            or (
                original in message
                and re.search(r"那个|这个|不要|不是|不选|选择|就选|[？?]", message)
            )
        ):
            raise QueryResolutionError(
                "当前候选请通过选择工具确认唯一完整名称或物品 ID，原查询上下文保持不变。"
            )
        if name == original:
            if pending.batch is None:
                raise QueryResolutionError(
                    "本次选择正在查询，请等待结果或明确提出无关的新物品查询。"
                )
            return batch
        return None

    def choose_confirmed(
        self,
        owner: TrustedOwner,
        batch_id: str,
        generation: str,
        item_id: int,
        *,
        event: object,
        text: str,
        retain: bool = False,
    ) -> MarketQuery:
        """Derive the choice from a new trusted event before checking model ID."""
        if (
            not isinstance(owner, TrustedOwner)
            or event is None
            or type(text) is not str
        ):
            raise QueryResolutionError("缺少可信用户消息绑定。", ErrorCode.UNSUPPORTED)
        self._prune()
        pending = self._pending.get(owner)
        if (
            pending is None
            or pending.batch is None
            or pending.generation != generation
            or pending.batch.batch_id != batch_id
            or event is pending.source_event
        ):
            raise QueryResolutionError(
                "候选确认无效或已过期，请重新查询或回复当前候选的完整名称。"
            )
        message = _confirmation_text(text)
        explicit = re.fullmatch(r"选择物品\s+([0-9]+)", message)
        if explicit:
            confirmed = int(explicit[1])
        else:
            matches = [
                candidate.item_id
                for candidate in pending.batch.candidates
                for name in (_confirmation_text(candidate.name),)
                if not _closed_reference(name)
                and message
                in {
                    name,
                    name + "那个",
                    name + " 那个",
                    *(
                        prefix + separator + name
                        for prefix in ("选", "选择", "就", "就选")
                        for separator in ("", " ")
                    ),
                }
            ]
            if len(matches) != 1:
                raise QueryResolutionError(
                    "请回复当前候选的唯一完整名称；疑问、否定、多项及序号不能确认选择。"
                )
            confirmed = matches[0]
        if type(item_id) is not int or item_id != confirmed:
            raise QueryResolutionError("模型提交的物品 ID 与真实用户确认不一致。")
        batch = pending.batch
        selected = self.choose(owner, batch_id, generation, confirmed, retain=retain)
        if retain:
            pending.consumed_batch = batch
            pending.confirmation_event = event
        return selected

    def _current(self, ticket: QueryTicket) -> _Pending:
        self._prune()
        pending = self._pending.get(ticket.owner)
        if pending is None or pending.generation != ticket.generation:
            raise QueryResolutionError("候选已失效，请重新查询。")
        return pending

    def _release_confirmed(self, ticket: QueryTicket) -> None:
        """Drop only this consumed flight's proof, including exceptional exits."""
        pending = self._pending.get(ticket.owner)
        if (
            pending is not None
            and pending.generation == ticket.generation
            and pending.consumed_batch is not None
        ):
            del self._pending[ticket.owner]

    def finish(
        self, ticket: QueryTicket, query: MarketQuery, *, retain: bool = False
    ) -> MarketQuery:
        self._current(ticket)
        if not retain:
            del self._pending[ticket.owner]
        return query

    def publish(
        self,
        ticket: QueryTicket,
        query: MarketQuery,
        candidates: tuple[ItemCandidate, ...],
        truncated: bool,
    ) -> CandidateBatch:
        pending = self._current(ticket)
        if type(candidates) is not tuple or any(
            not isinstance(c, ItemCandidate) for c in candidates
        ):
            raise QueryResolutionError("物品候选格式无效。", ErrorCode.UNPARSED)
        ids = tuple(c.item_id for c in candidates)
        if len(set(ids)) != len(ids) or any(
            c.item_id > 2_147_483_647 for c in candidates
        ):
            raise QueryResolutionError("物品候选 ID 无效。", ErrorCode.UNPARSED)
        if type(truncated) is not bool:
            raise QueryResolutionError("候选截断状态无效。", ErrorCode.UNPARSED)
        batch = CandidateBatch(
            secrets.token_urlsafe(24),
            ticket.generation,
            query,
            candidates[: self._max_candidates],
            truncated or len(candidates) > self._max_candidates,
        )
        pending.batch = batch
        return batch

    def choose(
        self,
        owner: TrustedOwner | None,
        batch_id: str,
        generation: str,
        item_id: int,
        *,
        retain: bool = False,
    ) -> MarketQuery:
        if not isinstance(owner, TrustedOwner):
            raise QueryResolutionError("缺少可信用户/会话绑定。", ErrorCode.UNSUPPORTED)
        self._prune()
        pending = self._pending.get(owner)
        if (
            pending is None
            or pending.batch is None
            or pending.generation != generation
            or pending.batch.batch_id != batch_id
        ):
            raise QueryResolutionError("候选批次无效或已过期，请重新查询。")
        if type(item_id) is not int or item_id not in tuple(
            c.item_id for c in pending.batch.candidates
        ):
            raise QueryResolutionError("请选择当前列表中的物品 ID。")
        selected = next(
            item for item in pending.batch.candidates if item.item_id == item_id
        )
        query = replace(
            pending.batch.query,
            query=str(item_id),
            item_id=item_id,
            original_query=pending.batch.query.query,
            item_name=selected.name,
        )
        if retain:
            pending.batch = (
                None  # Consume selection immediately, retain late-result fence.
            )
            pending.source_event = None
        else:
            del self._pending[owner]
        return query


class QueryCoordinator:
    """Start invalidates before parsing/awaits; choose reuses frozen defaults.

    Resolved values are local contracts. SourceHttpError/ItemPayloadError remain
    source exceptions for the future market SDK adapter to map without guessing.
    """

    def __init__(
        self, resolver: MarketQueryResolver, registry: CandidateRegistry
    ) -> None:
        self.resolver, self.registry = resolver, registry

    def start(self, owner: TrustedOwner | None) -> QueryTicket:
        return self.registry.begin(owner)

    async def resolve(
        self,
        owner: TrustedOwner | None,
        parameters: Mapping[str, object] | str,
        services: ModuleServices,
        source: ItemSearch,
        *,
        entry: str = "structured",
        ticket: QueryTicket | None = None,
        retain_until_result: bool = False,
    ) -> MarketQuery | CandidateBatch:
        if ticket is None:
            ticket = self.start(owner)
        elif ticket.owner != owner:
            raise QueryResolutionError("候选会话不匹配。")
        self.registry._current(ticket)
        if entry in ("structured", "page", "tool"):
            parameters = structured_parameters(parameters)
        elif entry == "command":
            parameters = command_parameters(parameters)
        elif entry == "natural":
            parameters = natural_parameters(parameters)
        else:
            raise QueryResolutionError("未知查询入口。")
        defaults = await QueryDefaults.capture(services)
        query = self.resolver.parse(parameters, defaults)
        if query.item_id is not None:
            return self.registry.finish(ticket, query, retain=retain_until_result)
        candidates, truncated = await search_item_candidates(source, query.query)
        if not candidates and not truncated:
            self.registry.finish(ticket, query)
            raise QueryResolutionError("没有找到匹配的物品。", ErrorCode.NOT_FOUND)
        if len(candidates) == 1 and not truncated:
            candidate = candidates[0]
            if (
                type(candidate.item_id) is not int
                or not 1 <= candidate.item_id <= 2_147_483_647
            ):
                raise QueryResolutionError("物品 ID 无效。", ErrorCode.UNPARSED)
            return self.registry.finish(
                ticket,
                replace(query, item_id=candidate.item_id, item_name=candidate.name),
                retain=retain_until_result,
            )
        return self.registry.publish(ticket, query, candidates, truncated)

    def choose(
        self, owner: TrustedOwner | None, batch_id: str, generation: str, item_id: int
    ) -> MarketQuery:
        return self.registry.choose(owner, batch_id, generation, item_id)
