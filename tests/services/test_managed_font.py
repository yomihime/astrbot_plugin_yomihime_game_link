"""Offline integrity, lifecycle, fallback and resource publication tests."""

import asyncio
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

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


class ManagedFontTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "resources/fonts"
        self.manager = fonts.ManagedFont(self.root)
        self.content = REFERENCE.read_bytes()

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp.cleanup()

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
        class Content:
            async def iter_chunked(inner, size):
                self.assertEqual(size, 65536)
                yield self.content

        class Response:
            status = 200
            headers = {}
            content_length = FONT_SIZE
            content = Content()

            async def __aenter__(inner):
                return inner

            async def __aexit__(inner, *args):
                pass

        class Session:
            def __init__(inner, **kwargs):
                self.assertFalse(kwargs["trust_env"])
                self.assertFalse(kwargs["auto_decompress"])
                self.assertEqual(kwargs["timeout"].total, 45)

            async def __aenter__(inner):
                return inner

            async def __aexit__(inner, *args):
                pass

            def get(inner, url, **kwargs):
                self.assertEqual(url, fonts.FONT_URL)
                self.assertFalse(kwargs["allow_redirects"])
                return Response()

        with patch("aiohttp.ClientSession", Session):
            self.manager.start()
            await self.manager._task
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
            (302, FONT_SIZE, self.content, {}),
            (404, FONT_SIZE, self.content, {}),
            (200, 7, b"invalid", {}),
            (200, None, self.content + b"x", {}),
            (200, None, b"x" * FONT_SIZE, {}),
            (200, FONT_SIZE, self.content, {"Content-Encoding": "gzip"}),
        )
        for status, length, payload, headers in cases:

            class Content:
                async def iter_chunked(inner, size):
                    for offset in range(0, len(payload), size):
                        yield payload[offset : offset + size]

            class Response:
                async def __aenter__(inner):
                    return inner

                async def __aexit__(inner, *args):
                    pass

            response = Response()
            response.status, response.content_length = status, length
            response.headers, response.content = headers, Content()

            class Session:
                def __init__(inner, **kwargs):
                    pass

                async def __aenter__(inner):
                    return inner

                async def __aexit__(inner, *args):
                    pass

                def get(inner, *args, **kwargs):
                    return response

            with patch("aiohttp.ClientSession", Session):
                await self.manager._download()
            self.assertEqual(self.manager.status()["error"], "download_failed")
            self.assertEqual(path.read_bytes(), self.content)
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
