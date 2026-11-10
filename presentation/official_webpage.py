"""Opt-in public official-page screenshots, never arbitrary URL rendering.

The optional display extension is safe to ignore on older Core versions. Only
reviewed official site profiles are implemented here; a module cannot supply
browser flags, selectors, cookies, scripts or an unrestricted URL.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
import socket
import sys
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from ..yomihime_game_link_sdk.display import LinksBlock, Privacy, UnknownBlock
from .webpage_images import (
    MAX_OUTPUT_BYTES,
    MAX_PAGE_HEIGHT,
    MAX_PIXELS,
    ImageBudgetError,
    capture_region,
    encode_page,
)
from .webpage_pagination import CutBoundary, ProtectedSpan, plan_pages

MARKER = "official_desktop_screenshot_v1"
MARKER_PATTERN = re.compile(MARKER + r":([0-9a-f]{64})\Z")
ARTICLE = re.compile(
    r"https://ff\.web\.sdo\.com/web8/index\.html#/newstab/newscont/[1-9][0-9]{0,11}\Z"
)
WIDTH = 1440
MAX_WIDTH = 1440
MAX_HEIGHT = 240000
MAX_PAGES = 8
MAX_RESPONSE = 4 * 1024 * 1024
MAX_TOTAL = 24 * 1024 * 1024
MAX_REQUESTS = 200
TIMEOUT = 120
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def installed_browsers():
    """Known installation executables only; never profiles, PATH or downloads.

    Only the environment running AstrBot is inspected. No shell expansion,
    user-configured executable, remote browser or Playwright cache is used.
    """
    candidates = []
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if not root or not Path(root).is_absolute():
                continue
            for suffix in (
                "Microsoft/Edge/Application/msedge.exe",
                "Google/Chrome/Application/chrome.exe",
            ):
                candidates.append(Path(root) / suffix)
    elif sys.platform.startswith("linux"):
        candidates = [
            Path(path)
            for path in (
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/opt/google/chrome/chrome",
                "/snap/bin/chromium",
                "/usr/local/bin/chromium",
            )
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path(path)
            for path in (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            )
        ]
    found = []
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                # Preserve launcher identity: Snap dispatches based on argv[0],
                # so resolving /snap/bin/chromium to /usr/bin/snap breaks it.
                value = str(candidate)
                if value not in found:
                    found.append(value)
        except OSError:
            continue
    return tuple(found[:3])


def requested_version(document):
    for block in document.ordered_blocks:
        if type(block) is UnknownBlock:
            match = MARKER_PATTERN.fullmatch(block.kind)
            if match:
                return match[1]
    return None


def requested_url(document):
    """A marker is a request, not authority to fetch arbitrary sites."""
    if document.privacy is not Privacy.PUBLIC:
        return None
    markers = [
        b
        for b in document.ordered_blocks
        if type(b) is UnknownBlock and MARKER_PATTERN.fullmatch(b.kind)
    ]
    if len(markers) != 1 or markers[0].required:
        return None
    links = [
        link
        for block in document.ordered_blocks
        if type(block) is LinksBlock
        for link in block.links
    ]
    if len(links) != 1 or not ARTICLE.fullmatch(links[0].url):
        return None
    return links[0].url


def without_screenshot_notice(document):
    return replace(
        document,
        ordered_blocks=tuple(
            b
            for b in document.ordered_blocks
            if not (type(b) is UnknownBlock and MARKER_PATTERN.fullmatch(b.kind))
        ),
    )


def resource_allowed(url):
    """Exact HTTPS hosts and resource paths, no auth, custom ports or redirects."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or "\\" in url
            or any(ord(c) < 32 for c in url)
        ):
            return False
    except ValueError:
        return False
    host, path = parsed.hostname, parsed.path
    if host == "ff.web.sdo.com":
        return path.startswith("/web8/") and "%" not in path and ".." not in path
    if host == "cqnews.web.sdo.com":
        return path in ("/api/news/newsDetail", "/api/news/newsList")
    if host == "static.web.sdo.com":
        return path.startswith("/jijiamobile/pic/ff14/")
    # Official article illustrations and page chrome, not analytics scripts.
    if host in ("ffstatic.web.sdo.com", "fu2.sdo.com", "fu5.web.sdo.com"):
        return path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
    return False


def public_addresses(records):
    if not records or any(
        not ipaddress.ip_address(r["host"]).is_global for r in records
    ):
        raise ValueError("official_page_address_denied")
    return records


def _detail_json(content, resource_url):
    """Unwrap an exact requested JSONP callback, without evaluating JavaScript.

    The official desktop client uses JSONP. The extracted JSON still has to
    pass the module's article identity/version validator before the original
    response can be fulfilled to that client.
    """
    query = parse_qs(
        urlsplit(resource_url).query,
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=16,
    )
    callbacks = query.get("callback")
    if callbacks is None:
        return content
    if len(callbacks) != 1 or not re.fullmatch(
        r"[A-Za-z_$][A-Za-z0-9_$]{0,127}", callbacks[0]
    ):
        raise ValueError("official_page_jsonp_invalid")
    match = re.fullmatch(
        rb"\s*" + re.escape(callbacks[0].encode("ascii")) + rb"\s*\((.*)\)\s*;?\s*",
        content,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError("official_page_jsonp_invalid")
    payload = match[1]
    # A greedy closing-parenthesis match must not hide trailing JavaScript
    # inside the candidate body. Require one complete JSON value here; the
    # module separately validates its article schema, identity and version.
    json.loads(payload)
    return payload


def normalize_png(content):
    """Compatibility name for the private browser encoder (PNG or JPEG)."""
    return encode_page(content).content


class CapturedPages(tuple):
    """Private image pages plus a non-secret explanation of the actual layout."""

    def __new__(cls, pages, metadata, notice=""):
        instance = super().__new__(cls, pages)
        instance.metadata = metadata
        instance.notice = notice
        return instance


# Geometry only from the independently launched, allowlisted public page. Text
# line rectangles and images are protected; no CSS/layout/content is rewritten.
CONTENT_BOUNDARIES = r"""({y, height}) => {
    const root = document.querySelector('.newsCont');
    const boundaries = new Map();
    const protectedSpans = [];
    const add = (v, priority=0) => {
        v = Math.round(v - y);
        if (v > 0 && v < height)
            boundaries.set(v, Math.max(priority, boundaries.get(v) || 0));
    };
    const protect = rect => {
        if (!rect.width || !rect.height) return;
        const a = Math.max(0, Math.floor(rect.top + scrollY - y));
        const b = Math.min(height, Math.ceil(rect.bottom + scrollY - y));
        if (b > a) protectedSpans.push([a,b]);
    };
    for (const el of root.querySelectorAll('img,svg,canvas,video,iframe'))
        protect(el.getBoundingClientRect());
    for (const el of root.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,tr,section,div')) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const priority = /^H[1-6]$/.test(el.tagName) ? 2 : 1;
        add(r.top + scrollY, priority); add(r.bottom + scrollY);
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node, count = 0;
    while (node = walker.nextNode()) {
        if (++count > 30000) throw Error('official_page_layout_budget');
        if (!node.textContent.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(node);
        for (const r of range.getClientRects()) {
            protect(r); add(Math.floor(r.top + scrollY));
            add(Math.ceil(r.bottom + scrollY));
        }
    }
    if (boundaries.size > 30000 || protectedSpans.length > 30000)
        throw Error('official_page_layout_budget');
    return {boundaries: [...boundaries], protected: protectedSpans};
}"""


class OfficialWebpageRenderer:
    """One short-lived anonymous browser per capture; no profile or disk cache."""

    def __init__(self):
        self._slot = asyncio.Semaphore(1)
        self._cache = OrderedDict()

    async def render(self, document, *, validate_detail=None, cache_scope=None):
        url = requested_url(document)
        if url is None or not callable(validate_detail):
            return None
        # Includes queueing, DNS, assets and browser startup in the same budget.
        async with asyncio.timeout(TIMEOUT):
            async with self._slot:
                key = (
                    url,
                    document.title,
                    requested_version(document),
                    validate_detail,
                    cache_scope,
                )
                cached = self._cache.get(key)
                if cached is not None and monotonic() - cached[0] < 120:
                    return cached[1]
                content = await self._capture(
                    url, document.title, requested_version(document), validate_detail
                )
                self._cache[key] = (monotonic(), content)
                self._cache.move_to_end(key)
                while (
                    len(self._cache) > 4
                    or sum(
                        sum(map(len, value[1]))
                        if isinstance(value[1], tuple)
                        else len(value[1])
                        for value in self._cache.values()
                    )
                    > 32 * 1024 * 1024
                ):
                    self._cache.popitem(last=False)
                return content

    async def _capture(self, url, title, version, validate_detail):
        executables = installed_browsers()
        if not executables:
            raise RuntimeError("existing_browser_unavailable")
        import aiohttp
        from playwright.async_api import async_playwright

        class PublicResolver(aiohttp.abc.AbstractResolver):
            def __init__(self):
                self.inner = aiohttp.resolver.DefaultResolver()

            async def resolve(self, host, port=0, family=socket.AF_INET):
                return public_addresses(await self.inner.resolve(host, port, family))

            async def close(self):
                await self.inner.close()

        resolver = PublicResolver()
        connector = aiohttp.TCPConnector(
            resolver=resolver, limit=6, use_dns_cache=False
        )
        try:
            async with (
                aiohttp.ClientSession(
                    connector=connector,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    trust_env=False,
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as session,
                async_playwright() as playwright,
            ):
                # A fresh temporary session, never an attached user's browser
                # or profile. Do not install browsers or fall back to bundled ones.
                browser = None
                for executable in executables:
                    try:
                        browser = await playwright.chromium.launch(
                            executable_path=executable,
                            headless=True,
                            chromium_sandbox=True,
                            proxy={
                                "server": "http://127.0.0.1:9",
                                "bypass": "<-loopback>",
                            },
                            args=[
                                "--disable-quic",
                                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                            ],
                            timeout=8000,
                        )
                    except Exception:
                        continue
                    break
                if browser is None:
                    raise RuntimeError("existing_browser_unavailable")
                try:
                    context = await browser.new_context(
                        viewport={"width": WIDTH, "height": 900},
                        device_scale_factor=1,
                        is_mobile=False,
                        has_touch=False,
                        user_agent=USER_AGENT,
                        service_workers="block",
                        accept_downloads=False,
                    )
                    total = requests = 0
                    verified_version = False
                    failed = False

                    async def route_request(route):
                        nonlocal total, requests, failed, verified_version
                        request = route.request
                        resource_url = request.url
                        # Official legacy article images use HTTP URLs. Upgrade
                        # only reviewed image hosts to TLS; never permit HTTP I/O.
                        parsed = urlsplit(resource_url)
                        if parsed.scheme == "http" and parsed.hostname in (
                            "fu2.sdo.com",
                            "fu5.web.sdo.com",
                            "ffstatic.web.sdo.com",
                        ):
                            resource_url = "https:" + resource_url[5:]
                        requests += 1
                        if request.method != "GET" or not resource_allowed(
                            resource_url
                        ):
                            # Required body images are verified below. Unrelated
                            # tracking/chrome requests need not poison the article.
                            await route.abort()
                            return
                        if requests > MAX_REQUESTS:
                            failed = True
                            await route.abort()
                            return
                        try:
                            async with session.get(
                                resource_url,
                                allow_redirects=False,
                                headers={"User-Agent": USER_AGENT, "Referer": url},
                            ) as response:
                                if response.status != 200:
                                    raise ValueError("official_page_resource_failed")
                                chunks = []
                                size = 0
                                async for chunk in response.content.iter_chunked(65536):
                                    size += len(chunk)
                                    total += len(chunk)
                                    if size > MAX_RESPONSE or total > MAX_TOTAL:
                                        raise ValueError(
                                            "official_page_resource_budget"
                                        )
                                    chunks.append(chunk)
                                content = b"".join(chunks)
                                if (
                                    urlsplit(resource_url).hostname
                                    == "cqnews.web.sdo.com"
                                    and urlsplit(resource_url).path
                                    == "/api/news/newsDetail"
                                ):
                                    if not validate_detail(
                                        _detail_json(content, resource_url),
                                        url.rsplit("/", 1)[-1],
                                        version,
                                    ):
                                        raise ValueError(
                                            "official_page_article_changed"
                                        )
                                    verified_version = True
                                await route.fulfill(
                                    status=200,
                                    body=content,
                                    headers={
                                        "content-type": response.headers.get(
                                            "Content-Type", "application/octet-stream"
                                        ),
                                        "access-control-allow-origin": "*",
                                    },
                                )
                        except Exception:
                            failed = True
                            await route.abort()

                    # HTTP requests are fulfilled by the DNS-pinned public-only
                    # client, never continued. The dead proxy and disabled
                    # non-proxied UDP also fence browser-side background traffic.
                    await context.route("**/*", route_request)
                    await context.route_web_socket("**/*", lambda ws: ws.close())
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_function(
                        """() => {
                            const body = document.querySelector('.newcontbox');
                            return body && (body.innerText.trim() || body.querySelector('img'))
                              && document.querySelector('.newstitle .tit')?.innerText.trim();
                        }""",
                        timeout=15000,
                    )
                    actual_title = await page.locator(".newstitle .tit").inner_text()
                    if "".join(actual_title.split()) != "".join(title.split()):
                        raise ValueError("official_page_article_changed")
                    # Trigger lazy images by scrolling through the bounded article.
                    for _ in range(math.ceil(MAX_HEIGHT / 800) + 1):
                        box = await page.locator(".newcontbox").bounding_box()
                        if not box or box["height"] > MAX_HEIGHT:
                            raise ValueError("official_page_image_budget")
                        await page.locator(".newcontbox").evaluate(
                            "el => el.querySelectorAll('img').forEach(i => i.loading = 'eager')"
                        )
                        await page.evaluate("window.scrollBy(0, 800)")
                        if await page.evaluate(
                            "window.scrollY + innerHeight >= document.querySelector('.newcontbox').getBoundingClientRect().bottom + scrollY"
                        ):
                            break
                    await page.wait_for_function(
                        """() => [...document.querySelectorAll('.newcontbox img')]
                        .every(i => i.complete && i.naturalWidth > 0)""",
                        timeout=7000,
                    )
                    await page.evaluate("document.fonts.ready")
                    await page.evaluate("window.scrollTo(0, 0)")
                    box = await page.locator(".newcontbox").bounding_box()
                    if not box or failed or not verified_version or page.url != url:
                        raise ValueError("official_page_incomplete")
                    container = await page.locator(".newsCont").bounding_box()
                    if not container:
                        raise ValueError("official_page_incomplete")
                    x = max(0, math.floor(container["x"]))
                    y = max(0, math.floor(container["y"]))
                    width = math.ceil(container["width"])
                    height = math.ceil(
                        max(
                            container["y"] + container["height"],
                            box["y"] + box["height"],
                        )
                        - y
                    )
                    if not (1 <= width <= MAX_WIDTH and 1 <= height <= MAX_HEIGHT):
                        raise ValueError("official_page_image_budget")
                    geometry = await page.evaluate(
                        CONTENT_BOUNDARIES, {"y": y, "height": height}
                    )
                    boundaries = tuple(CutBoundary(*v) for v in geometry["boundaries"])
                    protected = tuple(ProtectedSpan(*v) for v in geometry["protected"])
                    page_limit = min(MAX_PAGE_HEIGHT, MAX_PIXELS // width)
                    encoded_split = False
                    while True:
                        plan = plan_pages(
                            width,
                            height,
                            boundaries=boundaries,
                            protected=protected,
                            max_page_height=page_limit,
                            max_pages=MAX_PAGES,
                        )
                        pages, details = [], []
                        try:
                            for part in plan.pages:
                                encoded = await capture_region(
                                    page,
                                    x=x,
                                    y=y + part.offset,
                                    width=width,
                                    height=part.height,
                                )
                                pages.append(encoded.content)
                                details.append(
                                    {
                                        "offset": part.offset,
                                        "width": width,
                                        "height": part.height,
                                        "bytes": len(encoded.content),
                                        "format": encoded.format,
                                        "quality": encoded.quality,
                                        "png_bytes": encoded.png_bytes,
                                    }
                                )
                        except ImageBudgetError as exc:
                            if str(exc) != "official_page_encoded_budget":
                                raise
                            # Replan the complete article at safe boundaries, not
                            # a recursive mid-image crop. No partial output escapes.
                            page_limit = min(page_limit - 1, part.height * 9 // 10)
                            encoded_split = True
                            continue
                        if sum(map(len, pages)) > MAX_OUTPUT_BYTES:
                            raise ImageBudgetError("official_page_total_output_budget")
                        reason = "encoded_budget" if encoded_split else plan.reason
                        notice = ""
                        if "budget" in reason:
                            notice = (
                                f"截图因本地图片大小或解码内存预算分为{len(pages)}张，"
                                "已沿内容边界保留全文；这不是QQ平台上限。"
                            )
                        return CapturedPages(
                            pages,
                            {
                                "width": width,
                                "height": height,
                                "reason": reason,
                                "pages": details,
                                "protected_regions": len(protected),
                                "cuts": list(plan.cut_positions),
                                "cuts_cross_content": any(
                                    span.y0 < cut < span.y1
                                    for cut in plan.cut_positions
                                    for span in protected
                                ),
                                "capture_tile_height": 4096,
                            },
                            notice,
                        )

                finally:
                    await browser.close()
        finally:
            await resolver.close()
