"""Offline screenshot boundaries; no browser, network or real subscriptions."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from ygl_test_subject.modules.ff14.features.news import news_document
from ygl_test_subject.presentation.official_webpage import (
    OfficialWebpageRenderer,
    normalize_png,
    public_addresses,
    requested_url,
    resource_allowed,
    without_screenshot_notice,
)
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
)
from ygl_test_subject.services.core_runtime import CoreRuntime

from yomihime_game_link_sdk.display import DisplayLimits, Link, LinksBlock, Privacy

URL = "https://ff.web.sdo.com/web8/indexm.html#/newstab/newscont/395410"


def document():
    return news_document(
        {"id": "395410", "title": "公告标题", "summary": "维护已结束。"}
    )


class ScreenshotPolicyTests(unittest.TestCase):
    def test_explicit_marker_and_single_allowlisted_link_required(self):
        doc = document()
        self.assertEqual(requested_url(doc), URL)
        self.assertIsNone(requested_url(without_screenshot_notice(doc)))
        self.assertIsNone(requested_url(replace(doc, privacy=Privacy.PRIVATE)))
        for url in (
            "http://ff.web.sdo.com/web8/indexm.html#/newstab/newscont/1",
            "https://ff.web.sdo.com.evil.test/web8/indexm.html#/newstab/newscont/1",
            "https://127.0.0.1/",
            URL + "?redirect=http://localhost/",
            URL + "/../1",
        ):
            changed = replace(
                doc,
                ordered_blocks=(
                    LinksBlock((Link("原文", url),)),
                    doc.ordered_blocks[-1],
                ),
            )
            self.assertIsNone(requested_url(changed))

    def test_resource_allowlist_rejects_private_and_credential_paths(self):
        self.assertTrue(resource_allowed("https://ff.web.sdo.com/web8/assetsm/test.js"))
        self.assertTrue(
            resource_allowed("https://cqnews.web.sdo.com/api/news/newsDetail?id=1")
        )
        for url in (
            "file:///etc/passwd",
            "https://localhost/",
            "https://127.0.0.1/",
            "https://ff.web.sdo.com:8080/web8/test.js",
            "https://user:secret@ff.web.sdo.com/web8/test.js",
            "https://ff.web.sdo.com/web8/%2e%2e/private",
            "https://ff.web.sdo.com/web8/../private",
            "https://static.web.sdo.com/yxzm/js/ac.js",
            "https://fu2.sdo.com/test.js",
        ):
            self.assertFalse(resource_allowed(url), url)

    def test_dns_results_fail_closed_even_if_mixed_with_public(self):
        self.assertEqual(public_addresses([{"host": "8.8.8.8"}]), [{"host": "8.8.8.8"}])
        for addresses in (
            [],
            [{"host": "127.0.0.1"}],
            [{"host": "::1"}],
            [{"host": "169.254.169.254"}],
            [{"host": "8.8.8.8"}, {"host": "192.168.1.1"}],
        ):
            with self.assertRaises(ValueError):
                public_addresses(addresses)

    def test_png_dimensions_and_bytes_are_bounded(self):
        def png(width, height):
            out = BytesIO()
            Image.new("RGB", (width, height), "white").save(out, format="PNG")
            return out.getvalue()

        self.assertTrue(normalize_png(png(485, 100)).startswith(b"\x89PNG"))
        for data in (b"bad", png(486, 100), png(485, 8193)):
            with self.assertRaises(Exception):
                normalize_png(data)


class ScreenshotRenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_success_removes_notice_and_keeps_exact_message_fields(self):
        renderer = GenericDisplayRenderer(
            RenderingBounds(3000, 100, 32, 50, 1, 1, 1, 32, 8)
        )
        doc = document()
        failed = await renderer.render(doc, limits=DisplayLimits(1, 1048576))
        self.assertIn("网页截图暂不可用", failed.text)
        success = await renderer.render(
            without_screenshot_notice(doc), limits=DisplayLimits(1, 1048576)
        )
        self.assertEqual(success.text, "公告标题\n维护已结束。\n官方原文: " + URL)

    async def test_same_public_article_reuses_bounded_pixels_across_recipients(self):
        renderer = OfficialWebpageRenderer()
        renderer._capture = AsyncMock(return_value=b"synthetic pixels")
        self.assertEqual(await renderer.render(document()), b"synthetic pixels")
        self.assertEqual(await renderer.render(document()), b"synthetic pixels")
        self.assertEqual(renderer._capture.await_count, 1)

    async def test_cancel_is_not_swallowed(self):
        renderer = OfficialWebpageRenderer()
        renderer._capture = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await renderer.render(document())

    async def test_unrequested_document_never_starts_browser(self):
        renderer = OfficialWebpageRenderer()
        renderer._capture = AsyncMock()
        self.assertIsNone(await renderer.render(without_screenshot_notice(document())))
        renderer._capture.assert_not_called()

    async def test_runtime_rechecks_epoch_after_screenshot(self):
        state = SimpleNamespace(enabled=True, epoch=1)
        registry = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(module=lambda _: state)
        )

        async def capture(_):
            state.epoch = 2
            return b"png"

        runtime = SimpleNamespace(
            registry=registry,
            _closing=False,
            _official_webpage_renderer=SimpleNamespace(render=capture),
        )
        # Snapshot module instances are immutable in production; use distinct
        # snapshots here so mutation cannot accidentally change both epochs.
        count = 0

        def snapshot():
            nonlocal count
            count += 1
            return SimpleNamespace(
                module=lambda _: SimpleNamespace(enabled=True, epoch=count)
            )

        registry.snapshot = snapshot
        with self.assertRaises(Exception):
            await CoreRuntime._render_public_subscription_card(
                runtime, "ff14/ff14", document()
            )


class BrowserBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, *, height=500, title="公告标题", error=None):
        png = BytesIO()
        Image.new("RGB", (485, 712), "white").save(png, format="PNG")
        body = SimpleNamespace(
            bounding_box=AsyncMock(return_value={"y": 200, "height": height}),
            evaluate=AsyncMock(),
        )
        heading = SimpleNamespace(inner_text=AsyncMock(return_value=title))
        page = SimpleNamespace(
            url=URL,
            goto=AsyncMock(),
            wait_for_function=AsyncMock(),
            locator=lambda selector: body if selector == "#newsCon" else heading,
            evaluate=AsyncMock(return_value=True),
            screenshot=AsyncMock(return_value=png.getvalue(), side_effect=error),
        )
        context = SimpleNamespace(
            route=AsyncMock(),
            route_web_socket=AsyncMock(),
            new_page=AsyncMock(return_value=page),
        )
        browser = SimpleNamespace(
            new_context=AsyncMock(return_value=context), close=AsyncMock()
        )
        launch = AsyncMock(return_value=browser)

        @asynccontextmanager
        async def playwright():
            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        # Test the real capture orchestration without installing/starting browsers.
        fake_api = SimpleNamespace(async_playwright=playwright)
        with patch.dict("sys.modules", {"playwright.async_api": fake_api}):
            try:
                result = await OfficialWebpageRenderer()._capture(URL, "公告标题")
            finally:
                browser.close.assert_awaited_once()
        return result, page, launch

    async def test_capture_requests_mobile_actual_page_and_supports_image_only_body(
        self,
    ):
        result, page, launch = await self.capture()
        self.assertTrue(result.startswith(b"\x89PNG"))
        page.goto.assert_awaited_once_with(
            URL, wait_until="domcontentloaded", timeout=20000
        )
        readiness = page.wait_for_function.await_args_list[0].args[0]
        self.assertIn("body.querySelector('img')", readiness)
        page.screenshot.assert_awaited_once()
        self.assertEqual(page.screenshot.await_args.kwargs["clip"]["height"], 712)
        self.assertTrue(launch.await_args.kwargs["chromium_sandbox"])
        self.assertIn("--disable-quic", launch.await_args.kwargs["args"])

    async def test_oversize_article_does_not_silently_crop(self):
        with self.assertRaisesRegex(ValueError, "image_budget"):
            await self.capture(height=9000)

    async def test_changed_title_and_capture_failure_close_browser(self):
        with self.assertRaisesRegex(ValueError, "article_changed"):
            await self.capture(title="不同文章")
        with self.assertRaises(RuntimeError):
            await self.capture(error=RuntimeError("screenshot failed"))
