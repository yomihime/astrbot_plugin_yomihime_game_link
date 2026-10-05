"""Independent Core request authorities, effect fences and forged grant denial."""

import asyncio
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.api.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ygl_test_subject.api.services import ConfigTarget
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
    assert_generation_current,
)
from ygl_test_subject.services.admin_authorization import (
    AdminAuthorizationService,
    _digest,
)


class AdminSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.db = SQLiteDatabase(Path(self.temp.name) / "core.db")
        await self.db.executor.initialize()
        self.repo = SQLiteAdminCredentialRepository(self.db)
        self.auth = AdminAuthorizationService(self.repo)
        self.target = ConfigTarget("host", "game_link/core")
        self.resources = {self.target: {"default_region"}}
        self.source = self.auth.register_source(
            "test-host",
            resources=self.resources,
            operations={AdminOperation.UPDATE_CONFIG},
        )
        self.request = object()
        self.live = True

    async def asyncTearDown(self):
        self.auth.close()
        await self.db.executor.close()
        self.temp.cleanup()

    def issue(self):
        return self.source.issue(
            subject="synthetic",
            request=self.request,
            expiry=time.time() + 30,
            operations={AdminOperation.UPDATE_CONFIG},
            resources=self.resources,
            live=lambda request: self.live and request is self.request,
        )

    async def grant(self, context=None):
        return await self.auth.authorize(
            AdminOperation.UPDATE_CONFIG,
            invocation=None,
            context=context or self.issue(),
            resources=self.resources,
        )

    async def effect(self, grant):
        def effect(unit):
            assert_generation_current(unit, grant, AdminOperation.UPDATE_CONFIG)
            unit.execute(
                "INSERT INTO config_state(principal_id,module_id,revision) VALUES('test','x/y',1)"
            )

        return await self.db.executor.run_transaction(effect, begin_mode="IMMEDIATE")

    async def test_host_uninitialized_and_revoked_native_no_fake_key(self):
        before = await self.repo.current()
        grant = await self.grant()
        await self.auth.validate_generation(
            grant, operation=AdminOperation.UPDATE_CONFIG
        )
        self.assertEqual(await self.repo.current(), before)
        state = await self.repo.bootstrap(
            _digest("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        )
        await self.repo.revoke(state.generation)
        grant = await self.grant()
        await self.auth.validate_generation(
            grant, operation=AdminOperation.UPDATE_CONFIG
        )
        self.assertEqual((await self.repo.current()).status.value, "REVOKED")

    async def test_context_fields_cross_source_and_operation_do_not_prove(self):
        real = self.issue()
        fake = SimpleNamespace(
            adapter_id=real.adapter_id,
            request_id=real.request_id,
            session_id=real.session_id,
        )
        for context, operation, resources in (
            (fake, AdminOperation.UPDATE_CONFIG, self.resources),
            (real, AdminOperation.LIST_MODULES, self.resources),
            (real, AdminOperation.UPDATE_CONFIG, {self.target: {"secret"}}),
            (
                real,
                AdminOperation.UPDATE_CONFIG,
                {ConfigTarget("other", "game_link/core"): {"default_region"}},
            ),
        ):
            with self.assertRaises(AdminAuthorizationDenied):
                await self.auth.authorize(
                    operation, invocation=None, context=context, resources=resources
                )
        other = AdminAuthorizationService(self.repo)
        other.register_source(
            "test-host",
            resources=self.resources,
            operations={AdminOperation.UPDATE_CONFIG},
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await other.authorize(
                AdminOperation.UPDATE_CONFIG,
                invocation=None,
                context=real,
                resources=self.resources,
            )
        other.close()

    async def test_forged_grant_and_caller_callback_denied(self):
        grant = await self.grant()
        for candidate in (
            AdminAuthorizationGrant(grant.operation, grant.generation),
            replace(grant),
            AdminAuthorizationGrant(
                grant.operation,
                grant.generation,
                SimpleNamespace(check=lambda *_: None),
            ),
        ):
            with self.assertRaises(AdminAuthorizationDenied):
                await self.effect(candidate)
        with self.assertRaises(AdminAuthorizationDenied):
            await AdminAuthorizationService(self.repo).validate_generation(
                grant, operation=grant.operation
            )

    async def test_end_expiry_close_and_live_false_deny(self):
        context = self.issue()
        grant = await self.grant(context)
        self.source.end(context)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.effect(grant)
        context = self.issue()
        grant = await self.grant(context)
        self.live = False
        with self.assertRaises(AdminAuthorizationDenied):
            await self.effect(grant)
        self.live = True
        grant = await self.grant()
        self.source.close()
        with self.assertRaises(AdminAuthorizationDenied):
            await self.effect(grant)

    async def test_commit_rechecks_after_effect_and_rolls_back(self):
        context = self.issue()
        grant = await self.grant(context)

        def effect(unit):
            assert_generation_current(unit, grant, grant.operation)
            unit.execute(
                "INSERT INTO config_state(principal_id,module_id,revision) VALUES('test','x/y',1)"
            )
            self.source.end(context)

        with self.assertRaises(AdminAuthorizationDenied):
            await self.db.executor.run_transaction(effect, begin_mode="IMMEDIATE")
        self.assertEqual((await self.repo.current()).generation, 0)
        count = await self.db.executor.run_read(
            lambda u: u.execute("SELECT count(*) FROM config_state").fetchone()[0]
        )
        self.assertEqual(count, 0)

    async def test_pending_cancel_is_visible_before_executor_drain(self):
        entered, release = threading.Event(), threading.Event()
        blocker = asyncio.create_task(
            self.db.executor.run_transaction(
                lambda _: (entered.set(), release.wait(3))[0]
            )
        )
        await asyncio.to_thread(entered.wait, 3)
        started = asyncio.Event()

        async def request():
            grant = await self.grant()
            started.set()
            await self.effect(grant)

        task = asyncio.create_task(request())
        await started.wait()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await blocker
        count = await self.db.executor.run_read(
            lambda u: u.execute("SELECT count(*) FROM config_state").fetchone()[0]
        )
        self.assertEqual(count, 0)

    async def test_expired_request_and_cross_database_effect_denied(self):
        context = self.issue()
        grant = await self.grant(context)
        with patch(
            "ygl_test_subject.services.admin_sources.time",
            return_value=time.time() + 61,
        ):
            with self.assertRaises(AdminAuthorizationDenied):
                await self.effect(grant)
        other = SQLiteDatabase(Path(self.temp.name) / "other.db")
        await other.executor.initialize()
        try:
            with self.assertRaises(AdminAuthorizationDenied):
                await other.executor.run_transaction(
                    lambda u: assert_generation_current(u, grant, grant.operation)
                )
        finally:
            await other.executor.close()
        count = await self.db.executor.run_read(
            lambda u: u.execute("SELECT count(*) FROM config_state").fetchone()[0]
        )
        self.assertEqual(count, 0)

    async def test_pending_end_and_closed_authorizer_fence(self):
        entered, release = threading.Event(), threading.Event()
        blocker = asyncio.create_task(
            self.db.executor.run_transaction(
                lambda _: (entered.set(), release.wait(3))[0]
            )
        )
        await asyncio.to_thread(entered.wait, 3)
        context = self.issue()
        grant = await self.grant(context)
        pending = asyncio.create_task(self.effect(grant))
        await asyncio.sleep(0)
        self.source.end(context)
        release.set()
        with self.assertRaises(AdminAuthorizationDenied):
            await pending
        await blocker
        grant = await self.grant()
        self.auth.close()
        with self.assertRaises(AdminAuthorizationDenied):
            await self.effect(grant)

    async def test_commit_before_end_remains_committed(self):
        context = self.issue()
        grant = await self.grant(context)
        await self.effect(grant)
        self.source.end(context)
        count = await self.db.executor.run_read(
            lambda u: u.execute("SELECT count(*) FROM config_state").fetchone()[0]
        )
        self.assertEqual(count, 1)
