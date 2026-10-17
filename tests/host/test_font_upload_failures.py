"""Fixed Host auth/ASGI upload consumer; synthetic requests, no live instance."""

import asyncio
import unittest
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME
from ygl_test_subject.core.contracts.administration import AdminAuthorizationDenied
from ygl_test_subject.presentation.bundled_font import FONT_FILENAME, FONT_SIZE
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
from ygl_test_subject.services import managed_font as fonts

from tests.host import test_admin_pages as admin_fixture
from tests.services import test_managed_font as font_fixture


class FontUploadFailureTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the pinned real Host auth/dispatch fixture, not its complete suite.
    asyncSetUp = admin_fixture.AdminPagesTests.asyncSetUp
    asyncTearDown = admin_fixture.AdminPagesTests.asyncTearDown
    call = admin_fixture.AdminPagesTests.call

    async def upload(self, content):
        return await self.client.post(
            f"/api/v1/plugins/extensions/{PLUGIN_NAME}/admin/font-upload",
            headers=self.headers,
            files={"file": (FONT_FILENAME, content, "application/octet-stream")},
            follow_redirects=True,
        )

    async def test_valid_pinned_upload_parser_fault_is_safe_service_failure(self):
        manager = self.runtime._managed_font
        content = manager._reference
        before = manager._path.read_bytes()
        for fault in (
            BitmapUnavailable("dependency_missing"),
            OSError("private parser path secret"),
        ):
            with self.subTest(fault=type(fault).__name__):
                with patch.object(fonts.BundledFontProvider, "load", side_effect=fault):
                    response = await self.upload(content)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["code"], "operation_unavailable")
                self.assertEqual(response.json()["status"], "error")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("secret", response.text)
                self.assertNotIn(str(manager._root), response.text)
                self.assertEqual(manager._path.read_bytes(), before)
                self.assertEqual(self.pages.requests, {})
                self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_invalid_upload_stays_bad_request_before_parser(self):
        for content in (b"invalid length", b"x" * FONT_SIZE):
            with self.subTest(size=len(content)):
                with patch.object(
                    fonts.BundledFontProvider,
                    "load",
                    side_effect=AssertionError("invalid input reached parser"),
                ) as load:
                    response = await self.upload(content)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["code"], "invalid_config")
                self.assertEqual(response.json()["status"], "error")
                load.assert_not_called()
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_post_join_auth_loss_keeps_old_font_ready_and_request_cleanup(self):
        manager = self.runtime._managed_font
        entered, cancelled = asyncio.Event(), asyncio.Event()
        before = manager._path.read_bytes()
        provider = manager.provider()

        class Blocked(font_fixture.DownloadResponse):
            async def __aenter__(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        original = self.runtime._admin_source.check

        def check(*args, **kwargs):
            if cancelled.is_set():
                raise AdminAuthorizationDenied
            return original(*args, **kwargs)

        with (
            patch("aiohttp.ClientSession", font_fixture.DownloadSession([Blocked()])),
            patch.object(self.runtime._admin_source, "check", side_effect=check),
        ):
            manager._task = asyncio.create_task(fonts.ManagedFont._download(manager))
            await entered.wait()
            response = await self.upload(manager._reference)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["code"], "admin_authorization_denied")
        self.assertTrue(manager._task.cancelled())
        self.assertEqual(manager.status()["state"], "ready")
        self.assertIs(manager.provider(), provider)
        self.assertEqual(manager._path.read_bytes(), before)
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})
        # A subsequent normally authorized request remains usable.
        recovered = await self.upload(manager._reference)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["data"]["state"], "ready")

    async def test_upload_without_dashboard_proof_is_rejected_before_parser(self):
        with patch.object(
            fonts.BundledFontProvider,
            "load",
            side_effect=AssertionError("unauthorized input reached parser"),
        ) as load:
            response = await self.client.post(
                f"/api/v1/plugins/extensions/{PLUGIN_NAME}/admin/font-upload",
                headers={},
                files={
                    "file": (
                        FONT_FILENAME,
                        self.runtime._managed_font._reference,
                        "application/octet-stream",
                    )
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 401)
        load.assert_not_called()
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})
