"""Focused adapter fence tests, separate from native Core acceptance."""

from __future__ import annotations

import asyncio
import unittest

from ygl_test_subject.core.lifecycle import _ServiceLifetime
from ygl_test_subject.services.module_services import _LifetimePort

from yomihime_game_link_sdk.errors import InvalidInvocation
from yomihime_game_link_sdk.services import ConfigSnapshot


class ModuleLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_revocation_during_await_rejects_completed_delegate_result(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class Config:
            async def current(self):
                entered.set()
                await release.wait()
                return ConfigSnapshot(0, {})

        old = _ServiceLifetime(("sample", "sample/status", "old-install"))
        new = _ServiceLifetime(("sample", "sample/status", "new-install"))
        port = _LifetimePort(Config(), old, {"current"})
        flight = asyncio.create_task(port.current())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            old.revoke()
        finally:
            release.set()
        with self.assertRaises(InvalidInvocation) as caught:
            await flight
        self.assertEqual(caught.exception.code, "invalid_invocation")
        self.assertEqual(
            await _LifetimePort(Config(), new, {"current"}).current(),
            ConfigSnapshot(0, {}),
        )
        old.revoke()
        with self.assertRaises(InvalidInvocation):
            await port.current()
        new.check()
