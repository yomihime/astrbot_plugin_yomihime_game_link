"""Opt-in public official-page screenshots, never arbitrary URL rendering.

The optional display extension is safe to ignore on older Core versions. Only
reviewed official site profiles are implemented here; a module cannot supply
browser flags, selectors, cookies, scripts or an unrestricted URL.
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import re
import socket
from collections import OrderedDict
from dataclasses import replace
from io import BytesIO
from time import monotonic
from urllib.parse import urlsplit

from ..yomihime_game_link_sdk.display import LinksBlock, Privacy, UnknownBlock
from .card_bitmap import RenderedBitmap
from .png_validation import PngValidationLimits, validate_png

MARKER = "official_mobile_screenshot_v1"
ARTICLE = re.compile(
    r"https://ff\.web\.sdo\.com/web8/indexm\.html#/newstab/newscont/[1-9][0-9]{0,11}\Z"
)
WIDTH = 485
MAX_HEIGHT = 8192
MAX_RESPONSE = 4 * 1024 * 1024
MAX_TOTAL = 24 * 1024 * 1024
MAX_REQUESTS = 100
TIMEOUT = 35
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


def requested_url(document):
    """A marker is a request, not authority to fetch arbitrary sites."""
    if document.privacy is not Privacy.PUBLIC:
        return None
    markers = [
        b
        for b in document.ordered_blocks
        if type(b) is UnknownBlock and b.kind == MARKER
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
            if not (type(b) is UnknownBlock and b.kind == MARKER)
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


def normalize_png(content):
    from PIL import Image

    if not isinstance(content, bytes) or len(content) > MAX_RESPONSE:
        raise ValueError("official_page_image_budget")
    with Image.open(BytesIO(content)) as source:
        if (
            source.format != "PNG"
            or source.width != WIDTH
            or not 1 <= source.height <= MAX_HEIGHT
        ):
            raise ValueError("official_page_image_budget")
        # Strip browser metadata before Core's stricter PNG validation.
        image = source.convert("RGB")
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        bitmap = RenderedBitmap(output.getvalue(), image.width, image.height)
    validate_png(bitmap, limits=PngValidationLimits())
    return bitmap.content


class OfficialWebpageRenderer:
    """One short-lived anonymous browser per capture; no profile or disk cache."""

    def __init__(self):
        self._slot = asyncio.Semaphore(1)
        self._cache = OrderedDict()

    async def render(self, document):
        url = requested_url(document)
        if url is None:
            return None
        # Includes queueing, DNS, assets and browser startup in the same budget.
        async with asyncio.timeout(TIMEOUT):
            async with self._slot:
                key = (url, document.title)
                cached = self._cache.get(key)
                if cached is not None and monotonic() - cached[0] < 120:
                    return cached[1]
                content = await self._capture(url, document.title)
                self._cache[key] = (monotonic(), content)
                self._cache.move_to_end(key)
                while len(self._cache) > 4:
                    self._cache.popitem(last=False)
                return content

    async def _capture(self, url, title):
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
                # Never disable TLS verification or Chromium's sandbox. The Host
                # administrator installs Chromium explicitly; runtime downloads none.
                browser = await playwright.chromium.launch(
                    headless=True,
                    chromium_sandbox=True,
                    proxy={"server": "http://127.0.0.1:9", "bypass": "<-loopback>"},
                    args=[
                        "--disable-quic",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    ],
                )
                try:
                    context = await browser.new_context(
                        viewport={"width": WIDTH, "height": 900},
                        device_scale_factor=1,
                        is_mobile=True,
                        has_touch=True,
                        user_agent=USER_AGENT,
                        service_workers="block",
                        accept_downloads=False,
                    )
                    total = requests = 0
                    failed = False

                    async def route_request(route):
                        nonlocal total, requests, failed
                        request = route.request
                        requests += 1
                        if request.method != "GET" or not resource_allowed(request.url):
                            # Images not available through the reviewed HTTPS
                            # profile must not be silently presented as complete.
                            if request.resource_type == "image":
                                failed = True
                            await route.abort()
                            return
                        if requests > MAX_REQUESTS:
                            failed = True
                            await route.abort()
                            return
                        try:
                            async with session.get(
                                request.url,
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
                                await route.fulfill(
                                    status=200,
                                    body=b"".join(chunks),
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
                            const body = document.querySelector('#newsCon');
                            return body && (body.innerText.trim() || body.querySelector('img'))
                              && document.querySelector('.newstitle .tit')?.innerText.trim();
                        }""",
                        timeout=15000,
                    )
                    actual_title = await page.locator(".newstitle .tit").inner_text()
                    if "".join(actual_title.split()) != "".join(title.split()):
                        raise ValueError("official_page_article_changed")
                    # Trigger lazy images by scrolling through the bounded article.
                    for _ in range(10):
                        box = await page.locator("#newsCon").bounding_box()
                        if not box or box["height"] > MAX_HEIGHT:
                            raise ValueError("official_page_image_budget")
                        await page.locator("#newsCon").evaluate(
                            "el => el.querySelectorAll('img').forEach(i => i.loading = 'eager')"
                        )
                        await page.evaluate("window.scrollBy(0, 800)")
                        if await page.evaluate(
                            "window.scrollY + innerHeight >= document.querySelector('#newsCon').getBoundingClientRect().bottom + scrollY"
                        ):
                            break
                    await page.wait_for_function(
                        """() => [...document.querySelectorAll('#newsCon img')]
                        .every(i => i.complete && i.naturalWidth > 0)""",
                        timeout=7000,
                    )
                    await page.evaluate("document.fonts.ready")
                    await page.evaluate("window.scrollTo(0, 0)")
                    box = await page.locator("#newsCon").bounding_box()
                    if not box or failed or page.url != url:
                        raise ValueError("official_page_incomplete")
                    height = math.ceil(box["y"] + box["height"] + 12)
                    if not 1 <= height <= MAX_HEIGHT:
                        raise ValueError("official_page_image_budget")
                    # Capture the real top/header/title/date/body, excluding footer.
                    content = await page.screenshot(
                        type="png",
                        full_page=True,
                        clip={"x": 0, "y": 0, "width": WIDTH, "height": height},
                        animations="disabled",
                        timeout=5000,
                    )
                    return normalize_png(content)
                finally:
                    await browser.close()
        finally:
            await resolver.close()
