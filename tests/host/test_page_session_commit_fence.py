"""Real SQLite COMMIT rollback using the actual runtime-issued source live fence.

The external Host verifier is controlled, not a real ASGI Host acceptance test.
"""

import asyncio
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from types import SimpleNamespace as N
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.page_sessions import HostModulePageSessions
from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
from ygl_test_subject.core.contracts.administration import AdminAuthorizationDenied
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.module_pages import ModulePageGrant

from yomihime_game_link_sdk import ConfigTarget


class _Source:
    def __init__(self):
        self.active = []

    def issue(self, **kwargs):
        self.active.append(kwargs)
        return kwargs

    def end(self, context):
        self.active[:] = [item for item in self.active if item is not context]

    def check(self, context, *args):
        if not context["live"](context["request"]):
            raise AdminAuthorizationDenied


class PageCommitFenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-page-commit-")
        self.addCleanup(self.temp.cleanup)
        self.database = SQLiteDatabase(Path(self.temp.name) / "runtime.sqlite3")
        await self.database.executor.initialize()
        await self.database.executor.run_transaction(
            lambda unit: unit.execute("CREATE TABLE effects(value TEXT)").rowcount
        )

    async def asyncTearDown(self):
        await self.database.executor.close()

    async def scenario(self, change, *, monitor=None, primary_error=None):
        owner = "sample/demo"
        target = ConfigTarget("synthetic", owner)
        identity = N(runtime_id="runtime", module_epoch=2)
        module = N(
            enabled=True,
            epoch=2,
            manifest=N(
                pages=(
                    N(
                        route_id="config",
                        access="public_web",
                        config_fields=("field",),
                        credential_fields=(),
                    ),
                )
            ),
        )
        ops = N(
            config_principal_id="synthetic",
            ordinary_resources=lambda: {target: {"field"}},
            credential_resources=lambda **kwargs: {},
        )
        core = N(
            admin_operations=ops,
            management_available=True,
            registry=N(
                snapshot=lambda: N(modules={owner: module}),
                is_active=lambda actual: actual is module,
            ),
            lifecycle=N(
                state=lambda _: N(identity=identity, lifecycle=N(value="active")),
                current_identity=lambda _: identity,
            ),
        )
        runtime = object.__new__(AstrBotRuntime)
        runtime._core = core
        runtime._admin_source = _Source()
        runtime._generation = 1
        runtime._ready = True
        runtime._closing = False
        page_clock = [time.time()]
        runtime._page_sessions = HostModulePageSessions(
            wall_clock=lambda: page_clock[0]
        )

        entered, joining, second_cancel, release = (asyncio.Event() for _ in range(4))
        watchers, cancellations = [], []

        async def connected():
            current = asyncio.current_task()
            if monitor is None or current.get_name() != "ygl:module-page-disconnect":
                return False
            if not watchers:
                watchers.append(current)
            entered.set()
            while True:
                try:
                    await release.wait()
                    return True
                except asyncio.CancelledError:
                    cancellations.append(True)
                    if len(cancellations) == 1:
                        joining.set()
                        if monitor.startswith("revoke") or monitor == "fail-revoke":
                            valid[0] = False
                            if monitor == "revoke-origin":
                                valid[0] = True
                                origin[0] = "https://different.test"
                            elif monitor == "revoke-validator":
                                valid[0] = True
                                server._validate_dashboard_token = MethodType(
                                    lambda *_: None, server
                                )
                            elif monitor == "revoke-expiry":
                                valid[0] = True
                                dashboard_clock[0] += 31
                            elif monitor == "revoke-session":
                                valid[0] = True
                                runtime._page_sessions.revoke(
                                    proof,
                                    subject=subject[0],
                                    bearer_digest=runtime._page_sessions.bearer_digest(
                                        bearer[0]
                                    ),
                                )
                            if monitor != "fail-revoke":
                                return False
                        if monitor in {"fail", "fail-revoke"}:
                            raise RuntimeError("synthetic monitor failed")
                    elif len(cancellations) == 2 and monitor == "cancel-repeat":
                        second_cancel.set()
                    else:
                        raise

        server = N()
        server._validate_dashboard_token = MethodType(lambda *_: None, server)
        raw = N(
            is_disconnected=connected,
            app=N(state=N(dashboard_app_adapter=N(_dashboard_server=server))),
        )
        bearer = ["synthetic.token.value"]
        subject = ["synthetic"]
        valid = [True]
        origin = ["https://ui.test"]
        dashboard_clock = [time.time()]
        request = N(
            _request=raw,
            headers=N(
                getlist=lambda name: ["Bearer " + bearer[0]]
                if name == "authorization"
                else origin
                if name == "origin"
                else []
            ),
        )

        def verifier(*_):
            if not valid[0]:
                raise AdminAuthorizationDenied
            return raw, subject[0], dashboard_clock[0] + 30

        observations = []
        if monitor in {"starlette", "starlette-revoke"}:
            from starlette.requests import Request

            receive_calls = 0

            async def receive():
                nonlocal receive_calls
                current = asyncio.current_task()
                if current.get_name() != "ygl:module-page-disconnect":
                    return {"type": "http.request", "body": b"", "more_body": False}
                if not watchers:
                    watchers.append(current)
                receive_calls += 1
                if receive_calls == 1:
                    asyncio.get_running_loop().call_soon(current.cancel)
                else:
                    entered.set()
                await release.wait()
                return {"type": "http.disconnect"}

            raw = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/synthetic",
                    "headers": [],
                    "app": raw.app,
                },
                receive=receive,
            )
            request._request = raw

        binding = ModulePageGrant(
            owner, "config", "runtime", 2, frozenset({"field"}), frozenset()
        )
        proof = runtime._page_sessions.issue(
            subject="synthetic",
            bearer_digest=runtime._page_sessions.bearer_digest(bearer[0]),
            binding=binding,
            generation=1,
            expiry=time.time() + 30,
        )["page_session"]

        async def helper(ops, action, data, *, authorization, **kwargs):
            if monitor is not None:
                await asyncio.wait_for(entered.wait(), 3)
                if monitor.startswith("starlette"):
                    await asyncio.sleep(0)
                    observations.append((watchers[0].done(), watchers[0].cancelling()))
            if primary_error is not None:
                raise primary_error

            def write(unit):
                unit.execute("INSERT INTO effects VALUES('synthetic effect')")
                if change == "verifier":
                    valid[0] = False
                if change == "subject":
                    subject[0] = "different"
                if change == "session":
                    bearer[0] = "different.token.value"
                if change == "epoch":
                    module.epoch = 3
                if change == "expiry":
                    page_clock[0] += 31
                if change == "scope":
                    ops.ordinary_resources = lambda: {}

                @contextmanager
                def commit_guard():
                    if authorization["live"](raw) is not True:
                        raise AdminAuthorizationDenied
                    yield

                unit.add_commit_guard(commit_guard)

            await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")
            if monitor == "starlette-revoke":
                asyncio.get_running_loop().call_soon(valid.__setitem__, 0, False)
            return {"revision": 2}

        self.last_source, self.last_watchers = runtime._admin_source, watchers
        self.last_observations = observations
        task = None
        with (
            patch(
                "ygl_test_subject.adapters.astrbot.web_admin.verified_dashboard_request",
                verifier,
            ),
            patch(
                "ygl_test_subject.services.module_pages.module_page_operation", helper
            ),
            patch(
                "ygl_test_subject.adapters.astrbot.admin_pages.verified_dashboard_request",
                verifier,
            ),
            patch(
                "ygl_test_subject.adapters.astrbot.admin_pages.time",
                lambda: dashboard_clock[0],
            ),
            patch(
                "ygl_test_subject.adapters.astrbot.runtime.time",
                lambda: dashboard_clock[0],
            ),
        ):
            call = runtime.module_page_operation(
                "module-config-save",
                dict(
                    owner=owner,
                    page="config",
                    runtime_id="runtime",
                    module_epoch=2,
                    expected_revision=1,
                    values={"field": "synthetic"},
                    page_session=proof,
                ),
                request=request,
            )
            try:
                if monitor in {"cancel", "cancel-repeat"}:
                    task = asyncio.create_task(call)
                    await asyncio.wait_for(joining.wait(), 3)
                    task.cancel()
                    if monitor == "cancel-repeat":
                        await asyncio.wait_for(second_cancel.wait(), 3)
                        task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 3)
                    self.assertTrue(task.cancelled())
                    self.assertEqual(
                        len(cancellations), 3 if monitor == "cancel-repeat" else 2
                    )
                elif primary_error is not None:
                    with self.assertRaises(type(primary_error)) as raised:
                        await call
                    self.assertIs(raised.exception, primary_error)
                elif change or (monitor is not None and monitor.startswith("revoke")):
                    with self.assertRaises(AdminAuthorizationDenied):
                        await call
                elif monitor in {"fail-revoke", "starlette-revoke"}:
                    with self.assertRaises(AdminAuthorizationDenied):
                        await call
                elif monitor == "fail":
                    with self.assertRaisesRegex(
                        RuntimeError, "synthetic monitor failed"
                    ):
                        await call
                else:
                    await call
            finally:
                release.set()
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                runtime._page_sessions.close()
        return await self.database.executor.run_read(
            lambda unit: unit.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
        )

    async def test_unchanged_host_authority_commits(self):
        self.assertEqual(await self.scenario(None), 1)

    async def test_revoked_actual_verifier_rolls_back_before_commit(self):
        self.assertEqual(await self.scenario("verifier"), 0)

    async def test_changed_subject_rolls_back_before_commit(self):
        self.assertEqual(await self.scenario("subject"), 0)

    async def test_changed_bearer_session_rolls_back_before_commit(self):
        self.assertEqual(await self.scenario("session"), 0)

    async def test_changed_lifecycle_epoch_rolls_back_before_commit(self):
        self.assertEqual(await self.scenario("epoch"), 0)

    async def test_expired_page_session_rolls_back_before_commit(self):
        self.assertEqual(await self.scenario("expiry"), 0)

    async def test_revoked_current_field_scope_rolls_back_before_commit(self):
        self.assertEqual(await self.scenario("scope"), 0)

    def assert_request_released(self):
        self.assertEqual(self.last_source.active, [])
        self.assertTrue(all(task.done() for task in self.last_watchers))

    async def test_revocation_during_final_monitor_join_rejects_committed_result(self):
        self.assertEqual(await self.scenario(None, monitor="revoke"), 1)
        self.assert_request_released()

    async def test_external_cancellation_during_final_monitor_join_is_preserved(self):
        for monitor in ("cancel", "cancel-repeat"):
            with self.subTest(monitor=monitor):
                self.assertEqual(await self.scenario(None, monitor=monitor), 1)
                self.assert_request_released()
                await self.database.executor.run_transaction(
                    lambda unit: unit.execute("DELETE FROM effects").rowcount
                )

    async def test_primary_cas_failure_survives_failed_monitor_join(self):
        error = RevisionConflict("synthetic", 1, 2)
        self.assertEqual(
            await self.scenario(None, monitor="fail", primary_error=error), 0
        )
        self.assert_request_released()

    async def test_monitor_failure_rejects_success_without_repeating_effect(self):
        self.assertEqual(await self.scenario(None, monitor="fail"), 1)
        self.assert_request_released()

    async def test_final_join_checks_original_host_expiry_identity_and_page_proof(self):
        for monitor in (
            "revoke-origin",
            "revoke-validator",
            "revoke-expiry",
            "revoke-session",
        ):
            with self.subTest(monitor=monitor):
                self.assertEqual(await self.scenario(None, monitor=monitor), 1)
                self.assert_request_released()
                await self.database.executor.run_transaction(
                    lambda unit: unit.execute("DELETE FROM effects").rowcount
                )

    async def test_authorization_failure_has_priority_over_monitor_failure(self):
        self.assertEqual(await self.scenario(None, monitor="fail-revoke"), 1)
        self.assert_request_released()

    async def test_real_starlette_swallowed_monitor_cancel_stops_and_rechecks(self):
        for monitor in ("starlette", "starlette-revoke"):
            with self.subTest(monitor=monitor):
                self.assertEqual(await self.scenario(None, monitor=monitor), 1)
                self.assertEqual(self.last_observations, [(False, 1)])
                self.assert_request_released()
                await self.database.executor.run_transaction(
                    lambda unit: unit.execute("DELETE FROM effects").rowcount
                )
