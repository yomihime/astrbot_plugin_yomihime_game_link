"""Official CN news: bounded complete snapshots and durable three-event drains.

The endpoint is a public website JSON source, not a promised developer API.
External HTML is reduced to literal text; images and OutLink are never fetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser
from unicodedata import category
from zoneinfo import ZoneInfo

from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import DisplayDocument, Link, LinksBlock, TextBlock
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from yomihime_game_link_sdk.services import HttpRequest
from yomihime_game_link_sdk.storage import OwnerScope
from yomihime_game_link_sdk.subscriptions import (
    ComposedPublicEvent,
    EvaluationDecision,
    EvaluationEvent,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    PublicEventContribution,
    SubscriptionRequest,
)

from ..presentation import ff14_card

SOURCE = "ff14_news_official"
COLLECTOR = "ff14.news.collect"
TYPE = "ff14.news.official"
COMPOSER = "ff14.news.card"
ADMINS = "ff14_news_admins"
TARGETS = "ff14_news_targets"
CATEGORIES = "8324,8325,8326,8327,5309,5310,5311,5312,5313"
MAX_PAGES = 32
MAX_ARTICLES = 1600
MAX_REMEMBERED = 5000
MAX_RESPONSE_BYTES = 2_000_000
_ID = re.compile(r"[1-9][0-9]{0,11}\Z")
_COMPONENT = re.compile(r"[^\s:/\\\x00-\x1f\x7f]{1,128}\Z")


def validate_admins(value):
    if not isinstance(value, (list, tuple)) or len(value) > 20:
        raise ValueError(ADMINS)
    if any(type(entry) is not str for entry in value) or len(set(value)) != len(value):
        raise ValueError(ADMINS)
    for entry in value:
        if (
            not isinstance(entry, str)
            or len(entry.split(":")) != 2
            or not all(_COMPONENT.fullmatch(part) for part in entry.split(":"))
        ):
            raise ValueError(ADMINS)


def validate_targets(value):
    if not isinstance(value, (list, tuple)) or len(value) > 20:
        raise ValueError(TARGETS)
    if any(type(entry) is not str for entry in value) or len(set(value)) != len(value):
        raise ValueError(TARGETS)
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError(TARGETS)
        parts = entry.split(":")
        if (
            len(parts) != 3
            or parts[1] not in ("FriendMessage", "GroupMessage")
            or not all(_COMPONENT.fullmatch(part) for part in parts)
        ):
            raise ValueError(TARGETS)


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden += 1
        elif tag in ("p", "br", "div", "li") and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value, limit):
    if type(value) is not str or len(value) > 100_000:
        raise ValueError("news_text_invalid")
    parser = _Plain()
    parser.feed(value)
    text = " ".join("".join(parser.parts).split())
    # Match Core's card text boundary, including HTML-decoded format controls.
    # One malformed source string must not stall the shared news outbox.
    text = "".join(c for c in text if not category(c).startswith("C"))
    return text[:limit]


def article(row):
    if not isinstance(row, Mapping) or type(row.get("Id")) not in (int, str):
        raise ValueError("news_id_invalid")
    news_id = str(row["Id"])
    if not _ID.fullmatch(news_id):
        raise ValueError("news_id_invalid")
    title = plain(row.get("Title") or "", 160)
    if not title:
        title = f"官网新闻 {news_id}（官网未提供标题）"
    category = str(row.get("CategoryCode", ""))
    if not re.fullmatch(r"[0-9]{1,12}", category):
        category = "未知"
    # Official fields provide wall time without an offset. Label it explicitly;
    # never use it as a completeness cursor or substitute the collection time.
    published = row.get("PublishDate")
    try:
        published = (
            datetime.strptime(published, "%Y/%m/%d %H:%M:%S")
            .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            .isoformat()
        )
    except (ValueError, TypeError):
        published = "官网未提供可识别时间"
    summary = plain(row.get("Summary") or row.get("Content") or "", 200)
    return {
        "id": news_id,
        "title": title,
        "category": category,
        "published": published,
        "summary": summary or "官网未提供摘要。",
    }


def news_document(item):
    url = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/" + item["id"]
    lines = (
        f"类别：官网类别 {item['category']}",
        f"时间：{item['published']}",
        item["summary"],
    )
    return DisplayDocument(
        title="FF14 国服新闻",
        subject=item["title"],
        ordered_blocks=tuple(TextBlock(line) for line in lines)
        + (LinksBlock((Link("官方原文", url),)),),
        sources=("最终幻想14 国服官网",),
        card=ff14_card(
            item["title"],
            category="国服新闻",
            blocks=tuple(cards.Text(line) for line in lines) + (cards.Text(url),),
        ),
    )


class NewsCollector:
    def __init__(self, services, *, clock=lambda: datetime.now(UTC)):
        self.services = services
        self.clock = clock

    def normalize(self, parameters):
        if not isinstance(parameters, Mapping) or parameters:
            raise ValueError("news_parameters_invalid")
        return NormalizedInput({})

    async def collect(self, context, parameters, previous):
        self.normalize(parameters.values)
        if (
            context.invocation is None
            or context.key.collector_id != COLLECTOR
            or context.key.source_id != SOURCE
            or context.key.key_version != 1
            or context.key.scope != OwnerScope.public()
            or context.key.module_id != "ff14/ff14"
            or context.key.parameters != parameters
        ):
            raise ValueError("news_collection_invalid")
        now = self.clock()
        known_ids = None
        if previous is not None:
            stored = previous.payload.get("known_ids")
            if isinstance(stored, (list, tuple)):
                known_ids = set(stored)
            elif previous.completeness is ObservationCompleteness.COMPLETE:
                known_ids = {row["id"] for row in previous.payload.get("articles", ())}
        try:
            # A single bounded source phase; Core also fences the execution lease.
            async with asyncio.timeout(80):
                http = (await self.services.scopes.bind(context.invocation)).http
                rows = await self._read(http, known_ids=known_ids)
            payload = {"articles": rows, "known_ids": [row["id"] for row in rows]}
            completeness = ObservationCompleteness.COMPLETE
        except (
            ValueError,
            TimeoutError,
            OSError,
            SourceHttpError,
            ExceptionGroup,
        ) as exc:
            if isinstance(exc, SourceHttpError) and exc.code == "cancelled":
                raise
            payload = {"known_ids": None if known_ids is None else sorted(known_ids)}
            completeness = ObservationCompleteness.FAILED
        return Observation(
            uuid.uuid4().hex,
            context.key,
            1,
            None,
            now,
            completeness,
            tuple(row["id"] for row in payload.get("articles", ())),
            payload,
        )

    async def _json(self, http, path, query):
        response = await http.fetch(HttpRequest(SOURCE, path, query=tuple(query)))
        if response.status_code != 200 or len(response.body) > MAX_RESPONSE_BYTES:
            raise ValueError("news_source_failed")
        try:
            value = json.loads(response.body)
        except (ValueError, UnicodeError):
            raise ValueError("news_source_invalid") from None
        if not isinstance(value, dict) or str(value.get("Code")) != "0":
            raise ValueError("news_source_failed")
        return value

    async def _page(self, http, index):
        page = await self._json(
            http,
            "/api/news/newsList",
            (
                ("gameCode", "ff"),
                ("CategoryCode", CATEGORIES),
                ("pageIndex", str(index)),
                ("pageSize", "50"),
            ),
        )
        rows = page.get("Data")
        total, pages, count = (
            page.get(name) for name in ("TotalCount", "PageCount", "RecordCount")
        )
        if (
            type(rows) is not list
            or len(rows) > 50
            or any(type(v) is not int for v in (total, pages, count))
            or not 0 <= total <= MAX_ARTICLES
            or not 0 <= pages <= MAX_PAGES
            or count != len(rows)
            or (total and (pages < 1 or not rows))
            or (not total and (rows or pages not in (0, 1)))
        ):
            raise ValueError("news_pagination_invalid")
        return page

    async def _read(self, http, *, known_ids=None):
        first = await self._page(http, 0)
        rows = list(first["Data"])
        # Match Core's per-source concurrency bound. TaskGroup drains/cancels
        # siblings on failure or timeout before the collection can complete.
        for start in range(1, first["PageCount"], 2):
            tasks = []
            async with asyncio.TaskGroup() as group:
                for index in range(start, min(start + 2, first["PageCount"])):
                    tasks.append(group.create_task(self._page(http, index)))
            for task in tasks:
                page = task.result()
                if (page["TotalCount"], page["PageCount"]) != (
                    first["TotalCount"],
                    first["PageCount"],
                ):
                    raise ValueError("news_pagination_changed")
                rows.extend(page["Data"])
        ids = [str(row.get("Id")) for row in rows if isinstance(row, dict)]
        if len(rows) != first["TotalCount"] or len(set(ids)) != len(rows):
            raise ValueError("news_pagination_incomplete")
        # Detect insertions/reordering during traversal without relying on max ID.
        if await self._page(http, 0) != first:
            raise ValueError("news_pagination_changed")
        result = []
        for row in rows:
            # Baseline needs IDs, not hundreds of historical detail requests.
            # Later snapshots fetch a detail only for a newly discovered ID
            # lacking a list summary. A failed detail keeps the prior known set.
            if (
                (not row.get("Summary") or not row.get("Title"))
                and known_ids is not None
                and str(row.get("Id")) not in known_ids
            ):
                news_id = str(row.get("Id"))
                if not _ID.fullmatch(news_id):
                    raise ValueError("news_id_invalid")
                detail = await self._json(
                    http, "/api/news/newsDetail", (("gameCode", "ff"), ("id", news_id))
                )
                if (
                    not isinstance(detail.get("Data"), dict)
                    or str(detail["Data"].get("Id")) != news_id
                ):
                    raise ValueError("news_detail_invalid")
                row = detail["Data"]
            result.append(article(row))
        return result


class NewsEvaluator:
    def evaluate(self, subscription, observation, previous_state):
        state = previous_state.value if previous_state is not None else None
        if observation.completeness is not ObservationCompleteness.COMPLETE:
            return EvaluationDecision(state, False)
        rows = list(observation.payload["articles"])
        if state is None:
            return EvaluationDecision(
                {"seen": sorted(row["id"] for row in rows), "pending": []}, False
            )
        seen = set(state["seen"])
        pending = list(state["pending"])
        for row in rows:
            if row["id"] not in seen:
                pending.append(dict(row))
                seen.add(row["id"])
        if len(seen) > MAX_REMEMBERED or len(pending) > MAX_ARTICLES:
            raise ValueError("news_backlog_limit")
        selected, pending = pending[:3], pending[3:]
        events = []
        for item in selected:
            item = dict(item)
            # Business identity is the official ID, never edit time or subscription.
            key = "ff14:cn:news:" + item["id"]
            digest = hashlib.sha256(
                json.dumps(item, sort_keys=True).encode()
            ).hexdigest()[:16]
            events.append(
                EvaluationEvent(
                    key,
                    1,
                    news_document(item),
                    PublicEventContribution(COMPOSER, item["id"] + ":" + digest, item),
                )
            )
        return EvaluationDecision(
            {"seen": sorted(seen), "pending": pending}, False, events=tuple(events)
        )


class NewsComposer:
    def compose(self, contributions):
        if not contributions or any(c.composer_id != COMPOSER for c in contributions):
            raise ValueError("news_contribution_invalid")
        if len({c.payload["id"] for c in contributions}) != 1:
            raise ValueError("news_contribution_conflict")
        # Different captured edits may overlap before sealing. One deterministic
        # captured version is rendered; event identity remains the official ID.
        selected = min(contributions, key=lambda c: c.contribution_key)
        return ComposedPublicEvent(news_document(selected.payload))


class NewsAddHere:
    def __init__(self, services):
        self.services = services

    async def invoke(self, context, parameters):
        values = (await self.services.config.current()).values
        admins = values.get(ADMINS, ())
        validate_admins(admins)
        if (
            context.origin is not InvocationOrigin.COMMAND
            or context.parent_id is not None
            or not context.adapter_id
            or not context.actor_id
            or not context.conversation_id
            or not context.actor_id.startswith(context.adapter_id + ":")
            or context.actor_id not in admins
        ):
            return CapabilityResult(
                "ff14-news-denied",
                ResultStatus.ERROR,
                error=ErrorDetail(
                    ErrorCode.AUTH_REQUIRED, "仅新闻管理员可为当前会话添加订阅。"
                ),
            )
        if parameters:
            return CapabilityResult(
                "ff14-news-invalid",
                ResultStatus.ERROR,
                error=ErrorDetail(
                    ErrorCode.PARAMETER_ERROR, "addhere 不接受目标或其它参数。"
                ),
            )
        # Core validates the exact issued invocation and resolves its full route.
        await self.services.subscriptions.create_request(
            context,
            SubscriptionRequest(
                TYPE,
                {},
                {"kind": "official_news"},
                "instant",
                creation_key="ff14-news-addhere-v1",
            ),
        )
        return CapabilityResult(
            "ff14-news-subscribed",
            ResultStatus.SUCCESS,
            document=DisplayDocument(
                "FF14 国服新闻",
                "订阅设置",
                (
                    TextBlock(
                        "当前会话已订阅国服新闻。首次完整采集建立基线；重复添加不会新增订阅。"
                    ),
                ),
            ),
        )
