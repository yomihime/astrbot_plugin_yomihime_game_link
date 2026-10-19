"""Pinned Host exposes only bounded, actionable master-key rejection messages."""

from __future__ import annotations

import base64
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.infrastructure.managed_key_manager import ManagedKeyConflict

from tests.host import test_admin_pages as fixture


class MasterKeyRecoveryFeedbackTests(unittest.IsolatedAsyncioTestCase):
    fixture_parent = Path(
        os.environ.get("YGL_MANAGED_KEY_TEST_ROOT")
        or (Path.home() / "AppData" / "Local" if os.name == "nt" else Path.cwd())
    )
    asyncTearDown = fixture.AdminPagesTests.asyncTearDown
    call = fixture.AdminPagesTests.call

    async def asyncSetUp(self) -> None:
        old = os.environ.pop("YGL_SECRET_KEY", None)
        self.addCleanup(
            lambda: os.environ.pop("YGL_SECRET_KEY", None)
            if old is None
            else os.environ.__setitem__("YGL_SECRET_KEY", old)
        )
        self.assertTrue(self.fixture_parent.is_dir())
        await fixture.AdminPagesTests.asyncSetUp(self)

    async def test_known_restore_rejections_are_bounded_and_unknown_stays_uncertain(
        self,
    ) -> None:
        current = await self.call("master-key-status", {})
        self.assertEqual(current.status_code, 200, current.text)
        status = current.json()["data"]
        encoded = base64.b64encode(bytes(range(32))).decode()
        request = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
            "encoded_key": encoded,
        }
        expected = {
            "invalid_key": (
                400,
                "原密钥格式无效，请输入标准 Base64 编码的 32 字节密钥；旧凭据未更改。",
            ),
            "key_conflict": (
                409,
                "所填密钥与当前密钥不一致；旧凭据未更改，请核对后重试。",
            ),
            "key_does_not_decrypt_inventory": (
                422,
                "所填密钥未能解开现有凭据；旧凭据未被替换，请核对原密钥后重试。",
            ),
            "inventory_unknown": (
                503,
                "未取得成功确认，请刷新核对配置与版本后重试。",
            ),
            "encryption_component_unavailable": (
                503,
                "未取得成功确认，请刷新核对配置与版本后重试。",
            ),
        }
        manager = self.runtime.core_runtime.managed_keys
        self.assertIsNotNone(manager)
        for code, (http_status, message) in expected.items():
            with self.subTest(code=code):
                with patch.object(
                    manager, "restore", side_effect=ManagedKeyConflict(code)
                ):
                    response = await self.call("master-key-restore", request)
                self.assertEqual(response.status_code, http_status, response.text)
                data = response.json()
                self.assertEqual(data["status"], "error")
                self.assertEqual(data["code"], code)
                self.assertEqual(data["message"], message)
                self.assertNotIn(encoded, response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(self.pages.requests, {})
                self.assertEqual(self.runtime._key_admin_source._proofs, {})
        after = (await self.call("master-key-status", {})).json()["data"]
        self.assertEqual(after, status)
