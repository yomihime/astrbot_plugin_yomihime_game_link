"""Fixed Host auth/ASGI upload consumer; synthetic requests, no live instance."""

import unittest
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME
from ygl_test_subject.presentation.bundled_font import FONT_FILENAME, FONT_SIZE
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
from ygl_test_subject.services import managed_font as fonts

from tests.host import test_admin_pages as admin_fixture


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
