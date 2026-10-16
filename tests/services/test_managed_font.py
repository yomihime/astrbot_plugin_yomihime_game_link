"""Offline integrity, lifecycle, fallback and resource publication tests."""

import asyncio
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import aiohttp
from ygl_test_subject.presentation.bundled_font import (
    FONT_FILENAME,
    FONT_SHA256,
    FONT_SIZE,
)
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
from ygl_test_subject.presentation.system_font import SystemFontProvider, _cmap
from ygl_test_subject.services import managed_font as fonts

REFERENCE = (
    Path(__file__).resolve().parents[2] / "presentation/assets/fonts" / FONT_FILENAME
)


class DownloadSession:
    """Synthetic source outcomes; never opens a socket."""

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.options = None

    def __call__(self, **kwargs):
        self.options = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class DownloadResponse:
    def __init__(self, payload=b"", *, status=200, length=None, headers=None):
        self.payload, self.status, self.content_length = payload, status, length
        self.headers, self.content = headers or {}, self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for offset in range(0, len(self.payload), size):
            yield self.payload[offset : offset + size]


class ManagedFontTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "resources/fonts"
        self.manager = fonts.ManagedFont(self.root)
        self.content = REFERENCE.read_bytes()

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp.cleanup()

    async def test_primary_connection_failure_uses_next_fixed_source(self):
        session = DownloadSession(
            [
                aiohttp.ClientConnectionError("secret must not escape"),
                DownloadResponse(self.content, length=FONT_SIZE),
            ]
        )
        self.manager._closed = False
        with patch("aiohttp.ClientSession", session):
            await self.manager._download()
        self.assertEqual(self.manager.status()["state"], "ready")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual((self.root / FONT_FILENAME).read_bytes(), self.content)

    async def test_valid_persistent_snapshot_no_network_and_no_path_reopen(self):
        self.root.mkdir(parents=True)
        path = self.root / FONT_FILENAME
        path.write_bytes(self.content)

        async def forbidden(_):
            self.fail("canonical resource must not download")

        with patch.object(fonts.ManagedFont, "_download", forbidden):
            self.manager.start()
            await self.manager._task
        path.write_bytes(b"corrupt after verification")
        self.assertEqual(self.manager.status()["source"], "canonical")
        self.assertEqual(
            sha256(self.manager.provider()._font_bytes).hexdigest(), FONT_SHA256
        )
        self.assertTrue(self.manager.provider().load(16).getbbox("字体"))

    async def test_missing_and_invalid_try_background_once_system_fallback_close(self):
        self.root.mkdir(parents=True)
        (self.root / FONT_FILENAME).write_bytes(b"invalid")
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def download(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(fonts.ManagedFont, "_download", download):
            self.manager.start()
            self.assertFalse(entered.is_set())
            await entered.wait()
            task = self.manager._task
            self.manager.start()
            self.manager.retry()
            self.assertIs(self.manager._task, task)
            self.assertIn(self.manager.status()["source"], ("system", "none"))
            if self.manager.status()["source"] == "system":
                self.assertTrue(self.manager.provider().supports(ord("A")))
            await self.manager.close()
            self.assertTrue(cancelled.is_set())
            self.assertTrue(task.done())
            self.assertFalse(list(self.root.glob(".font-*")))
            with self.assertRaises(BitmapUnavailable):
                self.manager.provider()

    async def test_upload_integrity_atomic_install_and_authorization_recheck(self):
        entered = asyncio.Event()

        async def download(_):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(fonts.ManagedFont, "_download", download):
            self.manager.start()
            await entered.wait()
            for content in (b"bad", self.content[:-1], b"x" * FONT_SIZE):
                with self.assertRaises(ValueError):
                    await self.manager.upload(content, lambda: None)
            self.assertFalse((self.root / FONT_FILENAME).exists())
            checks = []
            result = await self.manager.upload(
                self.content, lambda: checks.append(True)
            )
        self.assertEqual(checks, [True])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["source"], "canonical")
        self.assertEqual((self.root / FONT_FILENAME).read_bytes(), self.content)
        self.assertFalse(list(self.root.glob(".font-*")))

        def denied():
            raise PermissionError

        with self.assertRaises(PermissionError):
            await self.manager.upload(self.content, denied)

    async def test_no_system_font_stays_text_capable_when_download_fails(self):
        async def failed(manager):
            manager._change("unavailable", "download_failed")

        with (
            patch.object(
                fonts,
                "SystemFontProvider",
                side_effect=BitmapUnavailable("font_unavailable"),
            ),
            patch.object(fonts.ManagedFont, "_download", failed),
        ):
            self.manager.start()
            await self.manager._task
        self.assertEqual(self.manager.status()["source"], "none")
        self.assertEqual(self.manager.status()["error"], "download_failed")
        with self.assertRaises(BitmapUnavailable):
            self.manager.provider()

    async def test_download_network_policy_and_size_validation(self):
        session = DownloadSession([DownloadResponse(self.content, length=FONT_SIZE)])
        with patch("aiohttp.ClientSession", session):
            self.manager.start()
            await self.manager._task
        self.assertFalse(session.options["trust_env"])
        self.assertFalse(session.options["auto_decompress"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(
            fonts.FONT_URLS,
            (
                "https://mirrors.tuna.tsinghua.edu.cn/CTAN/fonts/notocjksc/"
                "NotoSansCJKsc-Regular.otf",
                "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@"
                "523d033d6cb47f4a80c58a35753646f5c3608a78/"
                "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
                fonts.FONT_URL,
            ),
        )
        self.assertEqual(session.calls[0][0], fonts.FONT_URLS[0])
        options = session.calls[0][1]
        self.assertFalse(options["allow_redirects"])
        self.assertLessEqual(options["timeout"].total, 15)
        self.assertEqual(options["timeout"].connect, 5)
        self.assertEqual(options["timeout"].sock_read, 5)
        self.assertEqual(fonts.DOWNLOAD_TOTAL_SECONDS, 45)
        self.assertEqual(self.manager.status()["source"], "canonical")
        self.assertEqual(self.manager.status()["state"], "ready")

    def test_system_coverage_matches_independent_cmap_and_bounds(self):
        from io import BytesIO

        from fontTools.ttLib import TTFont

        try:
            provider = SystemFontProvider()
        except BitmapUnavailable:
            self.skipTest("no supported OS font available")
        with TTFont(BytesIO(provider._font_bytes), fontNumber=0) as face:
            expected = {
                cp for cp, name in face.getBestCmap().items() if face.getGlyphID(name)
            }
        self.assertEqual(provider._coverage, expected)
        self.assertFalse(provider.supports(True))
        self.assertFalse(provider.supports(0x10FFFF))
        self.assertEqual(len(_cmap(self.content)), 44810)
        for malformed in (b"", b"ttcf", b"\0" * 100):
            with self.assertRaises((ValueError, __import__("struct").error)):
                _cmap(malformed)

    async def test_rejected_downloads_preserve_existing_resource(self):
        self.root.mkdir(parents=True)
        path = self.root / FONT_FILENAME
        path.write_bytes(self.content)
        self.manager._closed = False
        cases = (
            (302, FONT_SIZE, self.content, {}, "download_http"),
            (404, FONT_SIZE, self.content, {}, "download_http"),
            (200, 7, b"invalid", {}, "download_length"),
            (200, None, self.content + b"x", {}, "download_length"),
            (200, None, b"x" * FONT_SIZE, {}, "download_digest"),
            (200, None, self.content[:-1], {}, "download_length"),
            (
                200,
                FONT_SIZE,
                self.content,
                {"Content-Encoding": "gzip"},
                "download_http",
            ),
        )
        for status, length, payload, headers, error in cases:
            response = DownloadResponse(
                payload, status=status, length=length, headers=headers
            )
            session = DownloadSession([response] * 3)
            with patch("aiohttp.ClientSession", session):
                await self.manager._download()
            self.assertEqual(self.manager.status()["error"], error)
            self.assertEqual([call[0] for call in session.calls], list(fonts.FONT_URLS))
            self.assertEqual(path.read_bytes(), self.content)
            self.assertFalse(list(self.root.glob(".font-*")))

    async def test_digest_mismatch_then_http_failure_uses_last_source(self):
        session = DownloadSession(
            [
                DownloadResponse(b"x" * FONT_SIZE),
                DownloadResponse(status=503),
                DownloadResponse(self.content),
            ]
        )
        self.manager._closed = False
        with patch("aiohttp.ClientSession", session):
            await self.manager._download()
        self.assertEqual([call[0] for call in session.calls], list(fonts.FONT_URLS))
        self.assertEqual(self.manager.status()["state"], "ready")
        self.assertEqual((self.root / FONT_FILENAME).read_bytes(), self.content)

    async def test_all_connections_fail_and_repeated_retry_is_deduplicated(self):
        session = DownloadSession(
            [aiohttp.ClientConnectionError("private proxy secret")] * 6
        )
        self.manager._closed = False
        with patch("aiohttp.ClientSession", session):
            self.manager.retry()
            first = self.manager._task
            self.manager.retry()
            self.assertIs(self.manager._task, first)
            await first
            self.assertEqual(self.manager.status()["error"], "download_connection")
            self.manager.retry()
            second = self.manager._task
            self.manager.retry()
            self.assertIs(self.manager._task, second)
            self.assertIsNot(first, second)
            await second
        self.assertEqual([call[0] for call in session.calls], list(fonts.FONT_URLS) * 2)
        self.assertNotIn("secret", repr(self.manager.status()))
        self.assertFalse((self.root / FONT_FILENAME).exists())

    async def test_source_timeout_leaves_next_source_opportunity(self):
        class Blocked(DownloadResponse):
            async def __aenter__(self):
                await asyncio.Event().wait()

        session = DownloadSession([Blocked(), DownloadResponse(self.content)])
        self.manager._closed = False
        with (
            patch("aiohttp.ClientSession", session),
            patch.object(fonts, "DOWNLOAD_SOURCE_SECONDS", 0.01),
        ):
            await self.manager._download()
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(self.manager.status()["state"], "ready")

    async def test_each_slow_source_gets_one_bounded_opportunity(self):
        class Blocked(DownloadResponse):
            async def __aenter__(self):
                await asyncio.Event().wait()

        session = DownloadSession([Blocked()] * 3)
        self.manager._closed = False
        loop = asyncio.get_running_loop()
        before = loop.time()
        with (
            patch("aiohttp.ClientSession", session),
            patch.object(fonts, "DOWNLOAD_SOURCE_SECONDS", 0.05),
            patch.object(fonts, "DOWNLOAD_TOTAL_SECONDS", 0.3),
        ):
            await self.manager._download()
        self.assertLess(loop.time() - before, 0.5)
        self.assertEqual([call[0] for call in session.calls], list(fonts.FONT_URLS))
        self.assertEqual(self.manager.status()["error"], "download_timeout")
        self.assertFalse((self.root / FONT_FILENAME).exists())

    async def test_total_deadline_stops_round_even_with_long_source_timeout(self):
        class Blocked(DownloadResponse):
            async def __aenter__(self):
                await asyncio.Event().wait()

        session = DownloadSession([Blocked()] * 3)
        self.manager._closed = False
        loop = asyncio.get_running_loop()
        before = loop.time()
        with (
            patch("aiohttp.ClientSession", session),
            patch.object(fonts, "DOWNLOAD_SOURCE_SECONDS", 1),
            patch.object(fonts, "DOWNLOAD_TOTAL_SECONDS", 0.05),
        ):
            await self.manager._download()
        self.assertLess(loop.time() - before, 0.5)
        self.assertEqual([call[0] for call in session.calls], [fonts.FONT_URLS[0]])
        self.assertEqual(self.manager.status()["error"], "download_timeout")

    async def test_cancel_download_does_not_attempt_backups_or_publish(self):
        entered = asyncio.Event()

        class Blocked(DownloadResponse):
            async def iter_chunked(self, size):
                yield b"partial"
                entered.set()
                await asyncio.Event().wait()

        session = DownloadSession([Blocked()])
        with patch("aiohttp.ClientSession", session):
            self.manager.start()
            await entered.wait()
            task = self.manager._task
            await self.manager.close()
        self.assertTrue(task.cancelled())
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(self.manager.status()["state"], "stopped")
        self.assertFalse((self.root / FONT_FILENAME).exists())

    async def test_upload_joins_actual_downloader_before_atomic_publication(self):
        entered, released = asyncio.Event(), asyncio.Event()

        class Blocked(DownloadResponse):
            async def __aenter__(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    released.set()

        session = DownloadSession([Blocked()])
        with patch("aiohttp.ClientSession", session):
            self.manager.start()
            await entered.wait()
            status = await self.manager.upload(
                self.content, lambda: self.assertTrue(released.is_set())
            )
            self.manager.retry()
        self.assertEqual(status["state"], "ready")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual((self.root / FONT_FILENAME).read_bytes(), self.content)
        self.assertFalse(list(self.root.glob(".font-*")))

    async def test_atomic_write_failure_preserves_resource_and_does_not_redownload(
        self,
    ):
        self.root.mkdir(parents=True)
        path = self.root / FONT_FILENAME
        path.write_bytes(self.content)
        session = DownloadSession([DownloadResponse(self.content)])
        self.manager._closed = False
        with (
            patch("aiohttp.ClientSession", session),
            patch.object(fonts.os, "replace", side_effect=OSError("private path")),
        ):
            await self.manager._download()
        self.assertEqual(self.manager.status()["error"], "download_write")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(path.read_bytes(), self.content)
        self.assertFalse(list(self.root.glob(".font-*")))

    async def test_verified_download_fontparse_failure_is_not_write_or_network_failure(
        self,
    ):
        retained_system_provider = object()
        self.manager._provider = retained_system_provider
        self.manager._source = "system"
        self.manager._closed = False
        for failure in (
            BitmapUnavailable("dependency_missing"),
            OSError("private path secret"),
        ):
            with self.subTest(failure=type(failure).__name__):
                session = DownloadSession([DownloadResponse(self.content)])
                with (
                    patch("aiohttp.ClientSession", session),
                    patch.object(
                        fonts.BundledFontProvider, "load", side_effect=failure
                    ),
                ):
                    await self.manager._download()
                self.assertEqual(self.manager.status()["error"], "download_fontparse")
                self.assertEqual(self.manager.status()["state"], "unavailable")
                self.assertEqual(self.manager.status()["source"], "system")
                self.assertIs(self.manager.provider(), retained_system_provider)
                self.assertEqual(len(session.calls), 1)
                self.assertFalse((self.root / FONT_FILENAME).exists())
                self.assertNotIn("secret", repr(self.manager.status()))
                self.assertFalse(self.root.exists())

    async def test_fontparse_failure_without_system_provider_retains_text_fallback(
        self,
    ):
        self.manager._closed = False
        session = DownloadSession([DownloadResponse(self.content)])
        with (
            patch("aiohttp.ClientSession", session),
            patch.object(
                fonts.BundledFontProvider,
                "load",
                side_effect=BitmapUnavailable("font_unavailable"),
            ),
        ):
            await self.manager._download()
        self.assertEqual(self.manager.status()["error"], "download_fontparse")
        self.assertEqual(self.manager.status()["source"], "none")
        self.test_injected_unavailable_provider_uses_complete_text_not_source_fixture()
        self.assertEqual(len(session.calls), 1)

    async def test_startup_parser_service_failure_preserves_system_and_persistent_bytes(
        self,
    ):
        self.root.mkdir(parents=True)
        path = self.root / FONT_FILENAME
        path.write_bytes(self.content)
        system_provider = self.manager._canonical(self.content)
        session = DownloadSession([DownloadResponse(self.content)])
        with (
            patch("aiohttp.ClientSession", session),
            patch.object(fonts, "SystemFontProvider", return_value=system_provider),
            patch.object(
                fonts.BundledFontProvider,
                "load",
                side_effect=BitmapUnavailable("dependency_missing"),
            ),
        ):
            self.manager.start()
            await self.manager._task
        self.assertEqual(self.manager.status()["error"], "download_fontparse")
        self.assertEqual(self.manager.status()["source"], "system")
        self.assertIs(self.manager.provider(), system_provider)
        self.assertEqual(path.read_bytes(), self.content)
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(list(self.root.glob(".font-*")))

    async def test_close_fences_upload_waiting_for_lock(self):
        self.manager._closed = False
        await self.manager._upload_lock.acquire()
        upload = asyncio.create_task(self.manager.upload(self.content, lambda: None))
        await asyncio.sleep(0)
        await self.manager.close()
        self.manager._upload_lock.release()
        with self.assertRaises(ValueError):
            await upload
        self.assertFalse((self.root / FONT_FILENAME).exists())

    def test_symlink_owned_directory_and_resource_rejected(self):
        destination = Path(self.temp.name) / "other"
        destination.mkdir()
        self.root.parent.mkdir(parents=True)
        try:
            self.root.symlink_to(destination, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation not supported")
        with self.assertRaises(ValueError):
            self.manager._install(self.content)
        self.root.unlink()
        self.root.mkdir()
        real = destination / FONT_FILENAME
        real.write_bytes(self.content)
        (self.root / FONT_FILENAME).symlink_to(real)
        with self.assertRaises(ValueError):
            self.manager._read()
        # Atomic replace replaces the link itself; never writes its target.
        self.manager._install(self.content)
        self.assertFalse((self.root / FONT_FILENAME).is_symlink())
        self.assertEqual(real.read_bytes(), self.content)

    def test_injected_unavailable_provider_uses_complete_text_not_source_fixture(self):
        from ygl_test_subject.presentation.command_card import CommandCardRenderer

        from yomihime_game_link_sdk.display import (
            DisplayAudience,
            DisplayDocument,
            TextBlock,
        )

        renderer = CommandCardRenderer(font_provider_factory=self.manager.provider)
        result = renderer(
            DisplayDocument("Title", "Subject", (TextBlock("Complete facts"),)),
            audience=DisplayAudience.PUBLIC,
        )
        self.assertIsNone(result.bitmap)
        self.assertIn("Complete facts", result.text)
        self.assertIsNone(renderer.help("Help\nComplete facts").bitmap)

    def test_windows_fixed_candidates_include_single_face_chinese_before_latin(self):
        from pathlib import PureWindowsPath

        from ygl_test_subject.presentation import system_font

        with (
            patch.object(system_font.os, "name", "nt"),
            patch.object(system_font, "Path", PureWindowsPath),
        ):
            paths = system_font.system_candidates()
        self.assertTrue(all(path.is_absolute() for path in paths))
        names = [path.name for path in paths]
        for name in ("simhei.ttf", "simkai.ttf", "simfang.ttf"):
            self.assertLess(names.index(name), names.index("arial.ttf"))
        with patch.object(system_font, "system_candidates", return_value=(REFERENCE,)):
            provider = SystemFontProvider()
        self.assertTrue(all(provider.supports(ord(char)) for char in "中文字体"))
        self.assertTrue(provider.load(16).getbbox("中文字体"))
