"""Official CN news: bounded complete snapshots and durable three-event drains.

The endpoint is a public website JSON source, not a promised developer API.
External HTML is reduced to literal excerpts; OutLink is never followed.
An optional display marker asks Core to capture the fixed official mobile page.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from unicodedata import category
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ....yomihime_game_link_sdk.contexts import InvocationOrigin
from ....yomihime_game_link_sdk.display import (
    DisplayDocument,
    Link,
    LinksBlock,
    TextBlock,
    UnknownBlock,
)
from ....yomihime_game_link_sdk.errors import SourceHttpError
from ....yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from ....yomihime_game_link_sdk.services import HttpRequest
from ....yomihime_game_link_sdk.storage import OwnerScope
from ....yomihime_game_link_sdk.subscriptions import (
    ComposedPublicEvent,
    EvaluationDecision,
    EvaluationEvent,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    PublicEventContribution,
    SubscriptionRequest,
)

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
DETAIL_BUDGET = 12
HEAD_REFRESH = 4
BODY_SNAPSHOT_CHARS = 1024
CATEGORY_NAMES = {"8325": "公告"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
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
    def __init__(self, paragraph_separator=" "):
        super().__init__(convert_charrefs=True)
        self.paragraph_separator = paragraph_separator
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden += 1
        elif tag in ("p", "br", "div", "li") and not self.hidden:
            self.parts.append(self.paragraph_separator)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden:
            self.parts.append(
                self.paragraph_separator if tag in ("p", "div", "li") else " "
            )

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


def body_excerpt(content, title, limit=200):
    """Take bounded literal body sentences, never invent a model-written summary.

    The list's Summary commonly repeats the title, so it is not a fallback.
    Keep facts in source order, omit standalone salutations/title/footer, and
    signal truncation rather than silently presenting a partial sentence as full.
    """
    if type(content) is not str or len(content) > 100_000:
        raise ValueError("news_text_invalid")
    parser = _Plain(paragraph_separator="\n")
    parser.feed(content)
    text = "".join(parser.parts)
    text = "".join(c for c in text if c.isspace() or not category(c).startswith("C"))
    title_key = "".join(c for c in title if c.isalnum())
    selected = []
    for piece in re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+|\n+", text):
        piece = " ".join(piece.split())
        key = "".join(c for c in piece if c.isalnum())
        if not key or key == title_key or (len(key) >= 8 and key in title_key):
            continue
        if re.fullmatch(
            r"(?:亲爱的|尊敬的)?(?:各位)?冒险者(?:们)?[：:，,！!。 ]*", piece
        ):
            continue
        if re.fullmatch(
            r"(?:《最终幻想14》|最终幻想14)?(?:中国|国服)?(?:运营|项目)团队[。 ]*",
            piece,
        ):
            continue
        if re.fullmatch(r"\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?", piece):
            continue
        remaining = limit - len(" ".join(selected)) - bool(selected)
        if len(piece) > remaining:
            if not selected:
                selected.append(piece[: limit - 1].rstrip() + "…")
            break
        selected.append(piece)
        if len(selected) == 3:
            break
    return " ".join(selected) or "暂时无法生成简述，请查看原文。"


class _Content(_Plain):
    """Semantic article body only; not page chrome, scripts or style changes."""

    def __init__(self):
        super().__init__(paragraph_separator="\n")
        self.resources = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if self.hidden:
            return
        field = "href" if tag == "a" else "src"
        if tag not in ("a", "img", "video", "audio", "source"):
            return
        value = dict(attrs).get(field)
        if not value:
            return
        parts = urlsplit(value)
        if parts.scheme not in ("", "https", "http"):
            return
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in ("_", "t", "timestamp", "cache", "spm")
            and not k.lower().startswith("utm_")
        ]
        value = urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path,
                urlencode(sorted(query)),
                parts.fragment,
            )
        )
        self.resources.append((tag, value))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "li"):
            self.parts.append("\n")


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def content_facts(content):
    """Return bounded snapshot plus hashes of the *complete* semantic body.

    Consumers checking a live screenshot must use article(detail)["version"].
    Dates/views/list order/HTML styling never enter that version. Resource URLs
    are compared as data only and never fetched here.
    """
    if type(content) is not str or len(content) > 100_000:
        raise ValueError("news_text_invalid")
    parser = _Content()
    parser.feed(content)
    text = "".join(parser.parts)
    text = "".join(c for c in text if c.isspace() or not category(c).startswith("C"))
    text = "\n".join(
        " ".join(line.split()) for line in text.splitlines() if line.strip()
    )
    return {
        "body_hash": _digest([" ".join(text.split()), parser.resources]),
        "resources_hash": _digest(parser.resources),
        "body_snapshot": text[:BODY_SNAPSHOT_CHARS],
        "body_truncated": len(text) > BODY_SNAPSHOT_CHARS,
    }


def change_excerpt(old, new):
    """Describe only observed literal differences, with explicit bounded fallback."""
    changes = []
    if old["title"] != new["title"]:
        changes.append("标题更新：" + new["title"][:80])
    if old["category"] != new["category"]:
        changes.append(
            "分类调整为："
            + CATEGORY_NAMES.get(new["category"], "官网分类 " + new["category"])
        )
    if old.get("body_hash") != new.get("body_hash"):
        body_change_start = len(changes)

        def split(text):
            return [
                p.strip() for p in re.split(r"(?<=[。！？!?])|\n+", text) if p.strip()
            ]

        before, after = (
            split(old.get("body_snapshot", "")),
            split(new.get("body_snapshot", "")),
        )
        for operation, a, b, c, d in SequenceMatcher(
            None, before, after, autojunk=False
        ).get_opcodes():
            if operation == "equal":
                continue
            if operation in ("insert", "replace"):
                changes.append("新增/修改：" + " ".join(after[c:d])[:100])
            if operation in ("delete", "replace"):
                changes.append("删除：" + " ".join(before[a:b])[:80])
            if len(changes) >= 3:
                break
        if old.get("resources_hash") != new.get("resources_hash"):
            changes.append("正文图片或链接发生变化，请查看原文。")
        if len(changes) == body_change_start:
            changes.append("正文保留片段之外有修改，请查看原文。")
    text = "；".join(changes) or "正文已更新，请查看原文。"
    return text if len(text) <= 260 else text[:259] + "…"


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
    summary = body_excerpt(row.get("Content") or "", title)
    facts = content_facts(row.get("Content") or "")
    complete = "Content" in row and type(row.get("Content")) is str
    version = _digest([title, category, facts["body_hash"]]) if complete else None
    return {
        "id": news_id,
        "title": title,
        "category": category,
        "published": published,
        "summary": summary,
        "version": version,
        "detail_complete": complete,
        **facts,
    }


def news_document(item):
    news_id = item["id"]
    if type(news_id) is not str or not _ID.fullmatch(news_id):
        raise ValueError("news_id_invalid")
    url = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/" + news_id
    category_label = CATEGORY_NAMES.get(
        item["category"], "官网分类 " + item["category"]
    )
    try:
        time_label = (
            datetime.fromisoformat(item["published"])
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .strftime("%Y-%m-%d %H:%M")
            + "（北京时间）"
        )
    except (ValueError, TypeError):
        time_label = item["published"]
    blocks = [
        TextBlock(
            "修改摘要：" + item["change_summary"]
            if item.get("change_summary")
            else item["summary"]
        ),
        LinksBlock((Link("官方原文", url),)),
    ]
    version = item.get("version")
    if type(version) is str and _HASH.fullmatch(version):
        blocks.append(
            UnknownBlock(
                kind="official_desktop_screenshot_v1:" + version,
                required=False,
                fallback_text="网页截图暂不可用，请查看原文。",
            )
        )
    return DisplayDocument(
        title=item["title"],
        subject="分类：" + category_label + " · 时间：" + time_label,
        ordered_blocks=tuple(blocks),
    )


class NewsCollector:
    def __init__(self, services, *, clock=lambda: datetime.now(UTC)):
        self.services = services
        self.clock = clock
        # Runtime-only progress across failed detail rounds. Keep normalized
        # public facts, never full HTML, cookies or bytes. This is not a cursor:
        # only a complete observation can advance durable known/seen state.
        self._detail_cache = {}

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
        prior = dict(previous.payload) if previous is not None else {}
        known_ids = prior.get("known_ids")
        if (
            known_ids is None
            and previous is not None
            and previous.completeness is ObservationCompleteness.COMPLETE
        ):
            known_ids = [row["id"] for row in prior.get("articles", ())]
        try:
            async with asyncio.timeout(80):
                http = (await self.services.scopes.bind(context.invocation)).http
                payload = await self._read(http, known_ids=known_ids, prior=prior)
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
            # Preserve the last committed refresh position and all confirmed
            # versions. Failed detail IO cannot erase the previous diff baseline.
            payload = {
                key: prior[key]
                for key in (
                    "details",
                    "refresh_cursor",
                    "poll_round",
                    "pending_new_ids",
                )
                if key in prior
            }
            payload["known_ids"] = known_ids
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

    async def _read(self, http, *, known_ids=None, prior=None):
        prior = prior or {}
        if known_ids is None:
            self._detail_cache.clear()
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
        by_id = {str(row["Id"]): row for row in rows}
        if any(not _ID.fullmatch(news_id) for news_id in by_id):
            raise ValueError("news_id_invalid")
        details = {
            key: dict(value)
            for key, value in prior.get("details", {}).items()
            if key in by_id
        }
        round_number = int(prior.get("poll_round", 0))
        cursor = str(prior.get("refresh_cursor", ""))
        pending_new_ids = [
            key for key in prior.get("pending_new_ids", ()) if key in by_id
        ]
        if known_ids is not None:
            known = set(known_ids)
            pending_new_ids = list(
                dict.fromkeys(
                    pending_new_ids
                    + [news_id for news_id in by_id if news_id not in known]
                )
            )
            fresh = pending_new_ids
            changed = [
                news_id
                for news_id in by_id
                if news_id in details
                and details[news_id].get("list_signature")
                != self._list_signature(by_id[news_id])
            ]
            # Poll the current headlines every round, rotate the archive as well.
            # A maximum scan costs 32 pages + one head check + 12 details,
            # below the source's 60 RPM cap. Concurrency remains two.
            priority = list(
                dict.fromkeys(fresh + changed + list(by_id)[:HEAD_REFRESH])
            )[:DETAIL_BUDGET]
            remaining = sorted((key for key in by_id if key not in priority), key=int)
            rotating = [
                key for key in remaining if not cursor or int(key) > int(cursor)
            ]
            rotating += [key for key in remaining if cursor and int(key) <= int(cursor)]
            selected_rotation = rotating[: max(0, DETAIL_BUDGET - len(priority))]
            selected = priority + selected_rotation
            for news_id in selected:
                row = by_id[news_id]
                cache_key = (news_id, _digest([round_number, row]))
                cached = self._detail_cache.get(cache_key)
                if cached is None:
                    detail = await self._json(
                        http,
                        "/api/news/newsDetail",
                        (("gameCode", "ff"), ("id", news_id)),
                    )
                    if (
                        not isinstance(detail.get("Data"), dict)
                        or str(detail["Data"].get("Id")) != news_id
                        or type(detail["Data"].get("Content")) is not str
                    ):
                        raise ValueError("news_detail_invalid")
                    cached = article(detail["Data"])
                    cached["list_signature"] = self._list_signature(row)
                    self._detail_cache[cache_key] = dict(cached)
                    while len(self._detail_cache) > MAX_ARTICLES:
                        del self._detail_cache[next(iter(self._detail_cache))]
                details[news_id] = dict(cached)
            pending_new_ids = [key for key in pending_new_ids if key not in selected]
            if selected_rotation:
                cursor = selected_rotation[-1]
        # A list record (even if it happens to contain Content) is not proof of
        # a complete detail response. Only confirmed details establish versions.
        result = [
            details[key]
            if key in details
            else article(
                {name: value for name, value in row.items() if name != "Content"}
            )
            for key, row in by_id.items()
        ]
        self._detail_cache.clear()
        return {
            "articles": result,
            "known_ids": list(by_id),
            "details": details,
            "refresh_cursor": cursor,
            "poll_round": round_number + 1,
            "pending_new_ids": pending_new_ids,
        }

    @staticmethod
    def _list_signature(row):
        return _digest(
            [plain(row.get("Title") or "", 160), str(row.get("CategoryCode", ""))]
        )


class NewsEvaluator:
    def evaluate(self, subscription, observation, previous_state):
        state = previous_state.value if previous_state is not None else None
        if observation.completeness is not ObservationCompleteness.COMPLETE:
            return EvaluationDecision(state, False)
        rows = list(observation.payload["articles"])
        if state is None:
            state = {
                "seen": [row["id"] for row in rows],
                "pending": [],
                "schema": 2,
                "latest": {},
                "versions": {},
            }
        seen = set(state["seen"])
        current_ids = {row["id"] for row in rows}
        latest = {
            key: dict(value)
            for key, value in state.get("latest", {}).items()
            if key in current_ids
        }
        versions = {
            key: list(value) for key, value in state.get("versions", {}).items()
        }
        pending = [dict(item) for item in state["pending"]]
        if state.get("schema") != 2:
            # Legacy pending IDs keep their already committed event identity.
            for item in pending:
                item["legacy_event"] = True
        for row in rows:
            item = dict(row)
            news_id, version = item["id"], item.get("version")
            if (
                not item.get("detail_complete")
                or type(version) is not str
                or not _HASH.fullmatch(version)
            ):
                continue
            old = latest.get(news_id)
            hashes = versions.setdefault(news_id, [])
            is_new = news_id not in seen
            if is_new or (hashes and version not in hashes):
                event_item = {
                    key: value
                    for key, value in item.items()
                    if key not in ("body_snapshot", "list_signature")
                }
                if not is_new:
                    event_item["change_summary"] = (
                        change_excerpt(old, item)
                        if old is not None
                        else "文章内容已有修改，旧正文不在当前保留范围内，请查看原文。"
                    )
                pending.append(event_item)
            # A known legacy/baseline ID with no body is learned silently.
            seen.add(news_id)
            if version not in hashes:
                hashes.append(version)
            latest[news_id] = item
        if (
            len(seen) > MAX_REMEMBERED
            or sum(map(len, versions.values())) > MAX_REMEMBERED
            or len(pending) > MAX_ARTICLES
        ):
            raise ValueError("news_backlog_limit")
        selected, pending = pending[:3], pending[3:]
        events = []
        for item in selected:
            key = "ff14:cn:news:" + item["id"]
            if not item.get("legacy_event"):
                key += ":" + item["version"]
            digest = _digest(item)[:16]
            events.append(
                EvaluationEvent(
                    key,
                    1,
                    news_document(item),
                    PublicEventContribution(COMPOSER, item["id"] + ":" + digest, item),
                )
            )
        return EvaluationDecision(
            {
                "schema": 2,
                "seen": sorted(seen),
                "latest": latest,
                "versions": versions,
                "pending": pending,
            },
            False,
            events=tuple(events),
        )


class NewsComposer:
    def compose(self, contributions):
        if not contributions or any(c.composer_id != COMPOSER for c in contributions):
            raise ValueError("news_contribution_invalid")
        if (
            len({(c.payload["id"], c.payload.get("version")) for c in contributions})
            != 1
        ):
            raise ValueError("news_contribution_conflict")
        # Contributions may differ in a recipient's previous diff baseline, but
        # only the same article/content version may share one public event.
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
