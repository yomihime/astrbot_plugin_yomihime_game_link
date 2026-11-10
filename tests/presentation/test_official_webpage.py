"""Offline screenshot boundaries; no browser, network or real subscriptions."""

import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from ygl_test_subject.modules.ff14.features.news import (
    NewsCollector,
    article,
    news_document,
)
from ygl_test_subject.presentation.official_webpage import (
    OfficialWebpageRenderer,
    _detail_json,
    installed_browsers,
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

URL = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/395410"


def document():
    return news_document(article(source_row()))


def source_row():
    return {
        "Id": 395410,
        "Title": "公告标题",
        "CategoryCode": "8325",
        "PublishDate": "2026/11/10 12:00:00",
        "Content": "<p>维护已结束。</p>",
    }


class ScreenshotPolicyTests(unittest.TestCase):
    def test_jsonp_keeps_module_article_identity_and_rejects_extra_script(self):
        raw = json.dumps({"Code": "0", "Data": source_row()}).encode()
        url = "https://cqnews.web.sdo.com/api/news/newsDetail?id=395410"
        self.assertEqual(_detail_json(raw, url), raw)
        wrapped = b"_jsonp_fixture(" + raw + b");"
        extracted = _detail_json(wrapped, url + "&callback=_jsonp_fixture")
        self.assertEqual(extracted, raw)
        self.assertTrue(
            NewsCollector.validate_screenshot_detail(
                extracted, "395410", article(source_row())["version"]
            )
        )
        for query, content in (
            ("&callback=", wrapped),
            ("&callback=x&callback=_jsonp_fixture", wrapped),
            ("&callback=window.fn", wrapped),
            ("&callback=other", wrapped),
            ("&callback=_jsonp_fixture", wrapped + b"alert(1);"),
            ("&callback=_jsonp_fixture", b"alert(1);" + wrapped),
        ):
            with self.subTest(query=query), self.assertRaises(ValueError):
                _detail_json(content, url + query)
        # A nested second call is not one JSON value inside the callback.
        with self.assertRaises(ValueError):
            _detail_json(
                b"_jsonp_fixture(" + raw + b");alert(1);_jsonp_fixture(" + raw + b");",
                url + "&callback=_jsonp_fixture",
            )

    def test_module_detail_validation_keeps_id_and_revision(self):
        validate = NewsCollector.validate_screenshot_detail
        row = source_row()
        version = article(row)["version"]

        def encoded(item):
            return json.dumps({"Code": "0", "Data": item}).encode()

        self.assertTrue(validate(encoded(row), "395410", version))
        self.assertFalse(validate(encoded(row), "395411", version))
        self.assertFalse(
            validate(encoded({**row, "Content": "changed"}), "395410", version)
        )
        self.assertFalse(validate(b"{}", "395410", version))

    def test_explicit_marker_and_single_allowlisted_link_required(self):
        doc = document()
        self.assertEqual(requested_url(doc), URL)
        self.assertIsNone(requested_url(without_screenshot_notice(doc)))
        self.assertIsNone(requested_url(replace(doc, privacy=Privacy.PRIVATE)))
        for url in (
            "http://ff.web.sdo.com/web8/index.html#/newstab/newscont/1",
            "https://ff.web.sdo.com.evil.test/web8/index.html#/newstab/newscont/1",
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

        self.assertTrue(normalize_png(png(1200, 100)).startswith(b"\x89PNG"))
        for data in (b"bad", png(1441, 100), png(1200, 5001)):
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
        self.assertIn("公告标题", success.text)
        self.assertIn("维护已结束。", success.text)
        self.assertIn("官方原文: " + URL, success.text)

    async def test_same_public_article_reuses_bounded_pixels_across_recipients(self):
        renderer = OfficialWebpageRenderer()
        renderer._capture = AsyncMock(return_value=b"synthetic pixels")
        self.assertEqual(
            await renderer.render(
                document(), validate_detail=NewsCollector.validate_screenshot_detail
            ),
            b"synthetic pixels",
        )
        self.assertEqual(
            await renderer.render(
                document(), validate_detail=NewsCollector.validate_screenshot_detail
            ),
            b"synthetic pixels",
        )
        self.assertEqual(renderer._capture.await_count, 1)

    async def test_cancel_is_not_swallowed(self):
        renderer = OfficialWebpageRenderer()
        renderer._capture = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await renderer.render(
                document(), validate_detail=NewsCollector.validate_screenshot_detail
            )

    async def test_cache_never_crosses_validator_or_module_epoch(self):
        renderer = OfficialWebpageRenderer()
        renderer._capture = AsyncMock(side_effect=(b"first", b"second", b"third"))
        validator = NewsCollector.validate_screenshot_detail

        def other_validator(*_):
            return True

        doc = document()
        self.assertEqual(
            await renderer.render(
                doc, validate_detail=validator, cache_scope=("ff14/ff14", 1)
            ),
            b"first",
        )
        self.assertEqual(
            await renderer.render(
                doc, validate_detail=validator, cache_scope=("ff14/ff14", 1)
            ),
            b"first",
        )
        self.assertEqual(
            await renderer.render(
                doc, validate_detail=other_validator, cache_scope=("ff14/ff14", 1)
            ),
            b"second",
        )
        self.assertEqual(
            await renderer.render(
                doc, validate_detail=validator, cache_scope=("ff14/ff14", 2)
            ),
            b"third",
        )
        self.assertEqual(renderer._capture.await_count, 3)

    async def test_unrequested_document_never_starts_browser(self):
        renderer = OfficialWebpageRenderer()
        renderer._capture = AsyncMock()
        self.assertIsNone(await renderer.render(without_screenshot_notice(document())))
        renderer._capture.assert_not_called()

    async def test_runtime_rechecks_epoch_after_screenshot(self):
        state = SimpleNamespace(
            enabled=True,
            epoch=1,
            handlers=SimpleNamespace(
                collectors={
                    "news": SimpleNamespace(
                        validate_screenshot_detail=NewsCollector.validate_screenshot_detail
                    )
                }
            ),
        )
        registry = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(module=lambda _: state)
        )

        async def capture(_, **_kwargs):
            state.epoch = 2
            return b"png"

        runtime = SimpleNamespace(
            registry=registry,
            _closing=False,
            _trusted_bundled_manifests={"ff14/ff14": object()},
            _official_webpage_renderer=SimpleNamespace(render=capture),
        )
        # Snapshot module instances are immutable in production; use distinct
        # snapshots here so mutation cannot accidentally change both epochs.
        count = 0

        def snapshot():
            nonlocal count
            count += 1
            return SimpleNamespace(
                module=lambda _: SimpleNamespace(
                    enabled=True,
                    epoch=count,
                    handlers=state.handlers,
                    manifest=object(),
                )
            )

        registry.snapshot = snapshot
        with (
            patch(
                "ygl_test_subject.services.core_runtime._matches_trusted_bundled_manifest",
                return_value=True,
            ),
            self.assertRaises(Exception),
        ):
            await CoreRuntime._render_public_subscription_card(
                runtime, "ff14/ff14", document()
            )


class BrowserBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def capture(
        self,
        *,
        height=500,
        title="公告标题",
        error=None,
        changed=False,
        wrong_id=False,
        jsonp=False,
    ):
        async def screenshot(**kwargs):
            if error:
                raise error
            clip = kwargs["clip"]
            png = BytesIO()
            Image.new("RGB", (clip["width"], clip["height"]), "white").save(
                png, format="PNG"
            )
            return png.getvalue()

        body = SimpleNamespace(
            bounding_box=AsyncMock(
                return_value={"x": 120, "y": 200, "width": 1200, "height": height}
            ),
            evaluate=AsyncMock(),
        )
        heading = SimpleNamespace(inner_text=AsyncMock(return_value=title))
        page = SimpleNamespace(
            url=URL,
            goto=AsyncMock(),
            wait_for_function=AsyncMock(),
            locator=lambda selector: (
                body if selector in (".newcontbox", ".newsCont") else heading
            ),
            evaluate=AsyncMock(return_value=True),
            screenshot=AsyncMock(side_effect=screenshot),
        )
        handlers = []

        async def register(_pattern, handler):
            handlers.append(handler)

        async def goto(*args, **kwargs):
            route = SimpleNamespace(
                request=SimpleNamespace(
                    method="GET",
                    resource_type="xhr",
                    url=(
                        "https://cqnews.web.sdo.com/api/news/newsDetail?id=395410"
                        + ("&callback=_jsonp_fixture" if jsonp else "")
                    ),
                ),
                fulfill=AsyncMock(),
                abort=AsyncMock(),
            )
            page.detail_route = route
            await handlers[0](route)

        page.goto.side_effect = goto

        async def chunks(_size):
            row = source_row()
            if changed:
                row["Content"] = "<p>新的正文</p>"
            if wrong_id:
                row["Id"] = 123
            content = json.dumps({"Code": "0", "Data": row}).encode()
            yield b"_jsonp_fixture(" + content + b");" if jsonp else content

        @asynccontextmanager
        async def get(*args, **kwargs):
            yield SimpleNamespace(
                status=200,
                headers={"Content-Type": "application/json"},
                content=SimpleNamespace(iter_chunked=chunks),
            )

        context = SimpleNamespace(
            route=AsyncMock(side_effect=register),
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
        with (
            patch.dict("sys.modules", {"playwright.async_api": fake_api}),
            patch("aiohttp.ClientSession.get", get),
            patch(
                "ygl_test_subject.presentation.official_webpage.installed_browsers",
                return_value=("/usr/bin/chromium",),
            ),
        ):
            try:
                result = await OfficialWebpageRenderer()._capture(
                    URL,
                    "公告标题",
                    article(source_row())["version"],
                    NewsCollector.validate_screenshot_detail,
                )
            finally:
                browser.close.assert_awaited_once()
        return result, page, launch

    async def test_official_jsonp_is_validated_then_fulfilled_unchanged(self):
        result, page, _ = await self.capture(jsonp=True)
        self.assertTrue(result[0].startswith(b"\x89PNG"))
        page.detail_route.abort.assert_not_awaited()
        self.assertTrue(
            page.detail_route.fulfill.await_args.kwargs["body"].startswith(
                b"_jsonp_fixture("
            )
        )
        with self.assertRaisesRegex(ValueError, "official_page_incomplete"):
            await self.capture(jsonp=True, changed=True)

    async def test_capture_requests_desktop_actual_page_and_supports_image_only_body(
        self,
    ):
        result, page, launch = await self.capture()
        self.assertTrue(result[0].startswith(b"\x89PNG"))
        page.goto.assert_awaited_once_with(
            URL, wait_until="domcontentloaded", timeout=20000
        )
        readiness = page.wait_for_function.await_args_list[0].args[0]
        self.assertIn("body.querySelector('img')", readiness)
        page.screenshot.assert_awaited_once()
        self.assertEqual(page.screenshot.await_args.kwargs["clip"]["height"], 500)
        self.assertTrue(launch.await_args.kwargs["chromium_sandbox"])
        self.assertEqual(
            launch.await_args.kwargs["executable_path"], "/usr/bin/chromium"
        )
        self.assertNotIn("channel", launch.await_args.kwargs)
        self.assertIn("--disable-quic", launch.await_args.kwargs["args"])

    async def test_oversize_article_does_not_silently_crop(self):
        with self.assertRaisesRegex(ValueError, "image_budget"):
            await self.capture(height=49000)

    async def test_changed_title_and_capture_failure_close_browser(self):
        with self.assertRaisesRegex(ValueError, "article_changed"):
            await self.capture(title="不同文章")
        with self.assertRaises(RuntimeError):
            await self.capture(error=RuntimeError("screenshot failed"))

    async def test_capture_cancellation_closes_browser_and_propagates(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.capture(error=asyncio.CancelledError())

    async def test_long_article_is_contiguous_ordered_pages_not_truncated(self):
        result, page, _ = await self.capture(height=12000)
        self.assertEqual(len(result), 3)
        clips = [call.kwargs["clip"] for call in page.screenshot.await_args_list]
        self.assertEqual(
            [(c["y"], c["height"]) for c in clips],
            [(200, 5000), (5200, 5000), (10200, 2000)],
        )

    async def test_changed_body_is_not_paired_with_older_summary(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            await self.capture(changed=True)

    async def test_different_article_id_with_identical_content_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            await self.capture(wrong_id=True)

    async def test_excessive_page_count_falls_back_as_a_whole(self):
        with self.assertRaisesRegex(ValueError, "image_budget"):
            await self.capture(height=45000)


class ExistingBrowserDiscoveryTests(unittest.TestCase):
    def test_linux_known_executables_only_and_deduplicated(self):
        with (
            patch("sys.platform", "linux"),
            patch("pathlib.Path.is_file", return_value=True),
            patch("os.access", return_value=True),
            patch("pathlib.Path.resolve", lambda p: p),
            patch.dict("os.environ", {"PATH": "/untrusted"}),
        ):
            paths = installed_browsers()
        self.assertEqual(paths[0].replace("\\", "/"), "/usr/bin/chromium")
        self.assertEqual(len(paths), 3)
        self.assertFalse(any("untrusted" in p or "ms-playwright" in p for p in paths))

    def test_windows_only_exact_edge_chrome_install_executables(self):
        with (
            patch("sys.platform", "win32"),
            patch.dict(
                "os.environ",
                {"PROGRAMFILES": "C:/program-files", "LOCALAPPDATA": "C:/local-apps"},
                clear=True,
            ),
            patch("pathlib.Path.is_file", return_value=True),
            patch("os.access", return_value=True),
            patch("pathlib.Path.resolve", lambda p: p),
        ):
            paths = installed_browsers()
        self.assertEqual(
            paths[:2],
            (
                str(
                    __import__("pathlib").Path(
                        "C:/program-files/Microsoft/Edge/Application/msedge.exe"
                    )
                ),
                str(
                    __import__("pathlib").Path(
                        "C:/program-files/Google/Chrome/Application/chrome.exe"
                    )
                ),
            ),
        )
        self.assertTrue(all(p.endswith(("msedge.exe", "chrome.exe")) for p in paths))
        self.assertFalse(any("User Data" in p for p in paths))

    def test_snap_launcher_path_is_not_resolved_to_snap_dispatcher(self):
        with (
            patch("sys.platform", "linux"),
            patch(
                "pathlib.Path.is_file",
                lambda p: str(p).replace("\\", "/") == "/snap/bin/chromium",
            ),
            patch("os.access", return_value=True),
            patch(
                "pathlib.Path.resolve",
                side_effect=AssertionError("must preserve launcher path"),
            ),
        ):
            self.assertEqual(
                tuple(p.replace("\\", "/") for p in installed_browsers()),
                ("/snap/bin/chromium",),
            )

    def test_missing_or_non_executable_installation_is_unsupported(self):
        for is_file, executable in ((False, True), (True, False)):
            with (
                patch("pathlib.Path.is_file", return_value=is_file),
                patch("os.access", return_value=executable),
            ):
                self.assertEqual(installed_browsers(), ())


class MissingBrowserFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_browser_does_not_even_import_or_start_playwright(self):
        start = AsyncMock()
        fake = SimpleNamespace(async_playwright=start)
        with (
            patch(
                "ygl_test_subject.presentation.official_webpage.installed_browsers",
                return_value=(),
            ),
            patch.dict("sys.modules", {"playwright.async_api": fake}),
        ):
            with self.assertRaisesRegex(RuntimeError, "existing_browser_unavailable"):
                await OfficialWebpageRenderer().render(
                    document(), validate_detail=NewsCollector.validate_screenshot_detail
                )
        start.assert_not_called()

    async def test_absent_optional_controller_does_not_install_anything(self):
        with (
            patch(
                "ygl_test_subject.presentation.official_webpage.installed_browsers",
                return_value=("/usr/bin/chromium",),
            ),
            patch.dict("sys.modules", {"playwright.async_api": None}),
        ):
            with self.assertRaises(ModuleNotFoundError):
                await OfficialWebpageRenderer().render(
                    document(), validate_detail=NewsCollector.validate_screenshot_detail
                )

    async def test_incompatible_installed_browsers_never_launch_default_download(self):
        launch = AsyncMock(side_effect=RuntimeError("incompatible browser"))

        @asynccontextmanager
        async def playwright():
            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        with (
            patch(
                "ygl_test_subject.presentation.official_webpage.installed_browsers",
                return_value=("/usr/bin/chromium", "/usr/bin/google-chrome"),
            ),
            patch.dict(
                "sys.modules",
                {"playwright.async_api": SimpleNamespace(async_playwright=playwright)},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "existing_browser_unavailable"):
                await OfficialWebpageRenderer().render(
                    document(), validate_detail=NewsCollector.validate_screenshot_detail
                )
        self.assertEqual(
            [call.kwargs["executable_path"] for call in launch.await_args_list],
            ["/usr/bin/chromium", "/usr/bin/google-chrome"],
        )
        self.assertTrue(
            all(call.kwargs["chromium_sandbox"] for call in launch.await_args_list)
        )
