"""Actual router/source/SQLite COMMIT with a controlled bound Host verifier.

No real Host, JWT key, credential reset or configuration mutation is executed.
The operation body writes only one synthetic effects row under source.fence.
"""

import asyncio
import tempfile
import threading
import time
import unittest
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as N
from unittest.mock import patch

from starlette.datastructures import URL, Headers
from ygl_test_subject.adapters.astrbot.admin_pages import AdminPages
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME
from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.admin_sources import TrustedAdminSource

from yomihime_game_link_sdk import ConfigTarget


class _Awaitable:
    def __await__(self):
        yield


class _Host:
    def __init__(self):
        self.valid, self.mode, self.subject = True, "normal", "synthetic-user"
        self.expiry = int(time.time()) + 120
        self.calls = 0
        self.coroutines = []
        self.revoke_on_call = None

    async def async_result(self):
        return {"username": self.subject, "exp": self.expiry}, ""

    def _validate_dashboard_token(self, token, path):
        self.calls += 1
        if self.calls == self.revoke_on_call:
            self.valid = False
        if self.mode == "exception":
            raise RuntimeError("SYNTHETIC EXCEPTION MUST NOT LEAK")
        if self.mode == "async":
            coroutine = self.async_result()
            self.coroutines.append(coroutine)
            return coroutine
        if self.mode == "awaitable":
            return _Awaitable()
        if self.mode == "malformed":
            return {"unexpected": True}
        if not self.valid:
            return None, "revoked"
        return {"username": self.subject, "exp": self.expiry}, ""


class _RequestProxy:
    def __init__(self, current):
        self.current = current

    def _get_current(self):
        return self.current.get()

    def __getattr__(self, name):
        return getattr(self.current.get(), name)


class GlobalAdminCommitFenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-global-fence-")
        self.addCleanup(self.temp.cleanup)
        self.database = SQLiteDatabase(Path(self.temp.name) / "synthetic.sqlite3")
        await self.database.executor.initialize()
        await self.database.executor.run_transaction(
            lambda unit: unit.execute("CREATE TABLE effects(value TEXT)").rowcount
        )
        target = ConfigTarget("synthetic", "game_link/core")
        self.resources = {target: frozenset({"synthetic"})}
        self.source = TrustedAdminSource(
            "controlled-global-host",
            self.resources,
            {AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
        )
        operations = N(
            **{
                name: lambda: self.resources
                for name in (
                    "ordinary_resources",
                    "credential_resources",
                    "master_key_resources",
                    "legacy_config_resources",
                    "module_resources",
                )
            }
        )
        self.core = N(admin_operations=operations)
        self.current = True
        self.runtime = N(
            management_entry=lambda **kw: (self.core, self.source),
            management_current=lambda core, source: self.current
            and core is self.core
            and source is self.source,
        )
        self.page = AdminPages(N(), self.runtime)
        self.page.closed = False
        self.request_context = ContextVar("synthetic_global_request")
        web = ModuleType("astrbot.api.web")
        web.request = _RequestProxy(self.request_context)
        web.json_response = lambda payload, status_code=200, **kw: N(
            status_code=status_code, payload=payload
        )
        self.modules = patch.dict(
            "sys.modules",
            {
                "astrbot": ModuleType("astrbot"),
                "astrbot.api": ModuleType("astrbot.api"),
                "astrbot.api.web": web,
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.count = 0

    async def asyncTearDown(self):
        self.page.close()
        self.source.close()
        await self.database.executor.close()

    def request(self, endpoint):
        host = _Host()
        path = f"/api/plug/{PLUGIN_NAME}/admin/{endpoint}"

        async def connected():
            return False

        raw = N(
            method="POST",
            url=URL("https://ui.test" + path),
            app=N(state=N(dashboard_app_adapter=N(_dashboard_server=host))),
            state=N(dashboard_g=N(username=host.subject)),
            is_disconnected=connected,
        )
        raw.app.state.dashboard_app_adapter._app = raw.app
        request = N(
            method="POST",
            path=path,
            headers=Headers(
                {
                    "authorization": "Bearer synthetic.token.value",
                    "origin": "https://ui.test",
                }
            ),
            username=host.subject,
            _request=raw,
        )
        return request, raw, host

    def change(self, mode, request, raw, host):
        if mode == "verifier":
            host.valid = False
        elif mode in {"exception", "async", "awaitable", "malformed"}:
            host.mode = mode
        elif mode == "subject":
            host.subject = request.username = raw.state.dashboard_g.username = (
                "other-user"
            )
        elif mode == "session":
            request.headers = Headers(
                {
                    "authorization": "Bearer another.token.value",
                    "origin": "https://ui.test",
                }
            )
        elif mode == "expiry":
            host.expiry = int(time.time()) - 1
        elif mode == "origin":
            request.headers = Headers(
                {
                    "authorization": "Bearer synthetic.token.value",
                    "origin": "https://other.test",
                }
            )
            raw.url = URL("https://other.test" + request.path)
        elif mode == "authority":
            raw.app.state.dashboard_app_adapter._dashboard_server = _Host()
        elif mode == "raw":
            request._request = N(**vars(raw))
        elif mode == "missing":
            del raw.app
        elif mode == "lifecycle":
            self.current = False
        elif mode == "page_close":
            self.page.close()
        elif mode == "source_close":
            self.source.close()

    async def effects(self, label):
        return await self.database.executor.run_read(
            lambda unit: unit.execute(
                "SELECT COUNT(*) FROM effects WHERE value=?", (label,)
            ).fetchone()[0]
        )

    async def scenario(self, endpoint, mode=None, phase="commit", wrapped=False):
        request, raw, host = self.request(endpoint)
        if mode == "issue":
            host.revoke_on_call = 2
        self.count += 1
        label = str(self.count)
        entered = []

        async def body(actual):
            await asyncio.sleep(0)
            if phase == "body":
                self.change(mode, request, raw, host)
            return {}

        async def invoke(_endpoint, core, data, context):
            await asyncio.sleep(0)
            if mode == "cas":
                raise RevisionConflict("synthetic", 1, 2)

            def write(unit):
                entered.append(True)
                unit.execute("INSERT INTO effects VALUES(?)", (label,))
                if phase == "commit":
                    self.change(mode, request, raw, host)
                unit.add_commit_guard(
                    lambda: self.source.fence(
                        context, AdminOperation.UPDATE_CONFIG, self.resources
                    )
                )

            try:
                await self.database.executor.run_transaction(
                    write, begin_mode="IMMEDIATE"
                )
            except Exception:
                if wrapped:
                    raise RuntimeError("SYNTHETIC COORDINATOR WRAP") from None
                raise
            if phase == "result":
                await asyncio.sleep(0)
                self.change(mode, request, raw, host)
            if phase == "cleanup":
                asyncio.get_running_loop().call_soon(
                    self.change, mode, request, raw, host
                )
            return {"synthetic_effect": True}

        self.page._invoke = invoke
        token = self.request_context.set(request)
        try:
            with patch(
                "ygl_test_subject.adapters.astrbot.admin_pages.bounded_body", body
            ):
                response = await self.page._handler(endpoint)()
        finally:
            self.request_context.reset(token)
        self.assertEqual(self.page.requests, {})
        self.assertEqual(self.source._proofs, {})
        self.assertNotIn("SYNTHETIC", str(response.payload))
        for coroutine in host.coroutines:
            self.assertIsNone(coroutine.cr_frame)
        return response, await self.effects(label), host.calls, bool(entered)

    async def test_three_original_global_routes_commit_under_normal_session(self):
        for endpoint in ("master-key-reset", "legacy-config-execute", "update"):
            with self.subTest(endpoint=endpoint):
                response, effects, calls, entered = await self.scenario(endpoint)
                self.assertEqual(
                    (response.status_code, effects, entered), (200, 1, True)
                )
                self.assertGreaterEqual(calls, 6)

    async def test_three_original_routes_reverify_at_sql_commit_and_roll_back(self):
        for endpoint in ("master-key-reset", "legacy-config-execute", "update"):
            with self.subTest(endpoint=endpoint):
                response, effects, calls, entered = await self.scenario(
                    endpoint, "verifier"
                )
                self.assertEqual(
                    (response.status_code, effects, entered), (403, 0, True)
                )
                self.assertGreater(calls, 1)

    async def test_session_subject_origin_authority_raw_and_expiry_changes_reject_commit(
        self,
    ):
        for mode in (
            "subject",
            "session",
            "origin",
            "authority",
            "raw",
            "expiry",
            "missing",
        ):
            with self.subTest(mode=mode):
                response, effects, _, entered = await self.scenario("update", mode)
                self.assertEqual(
                    (response.status_code, effects, entered), (403, 0, True)
                )

    async def test_verifier_exception_coroutine_awaitable_and_malformed_result_fail_closed(
        self,
    ):
        for mode in ("exception", "async", "awaitable", "malformed"):
            with self.subTest(mode=mode):
                response, effects, _, entered = await self.scenario("update", mode)
                self.assertEqual(
                    (response.status_code, effects, entered), (403, 0, True)
                )

    async def test_revocation_during_body_never_enters_operation(self):
        response, effects, _, entered = await self.scenario(
            "update", "verifier", "body"
        )
        self.assertEqual((response.status_code, effects, entered), (403, 0, False))

    async def test_revocation_at_issue_cleans_the_unreturned_proof(self):
        response, effects, calls, entered = await self.scenario("update", "issue")
        self.assertEqual((response.status_code, effects, entered), (403, 0, False))
        self.assertEqual(calls, 2)

    async def test_concurrent_requests_keep_independent_session_fences(self):
        requests = {}
        for label in ("left", "right"):
            request, raw, host = self.request("update")
            request.case = label
            requests[label] = request, raw, host
        started = set()
        both = asyncio.Event()

        async def body(request):
            await asyncio.sleep(0)
            return {"case": request.case}

        async def invoke(endpoint, core, data, context):
            label = data["case"]
            started.add(label)
            if len(started) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 3)

            def write(unit):
                unit.execute("INSERT INTO effects VALUES(?)", (label,))
                if label == "left":
                    requests[label][2].valid = False
                unit.add_commit_guard(
                    lambda: self.source.fence(
                        context, AdminOperation.UPDATE_CONFIG, self.resources
                    )
                )

            await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")
            return {"synthetic_effect": True}

        async def call(request):
            token = self.request_context.set(request)
            try:
                return await self.page._handler("update")()
            finally:
                self.request_context.reset(token)

        self.page._invoke = invoke
        with patch("ygl_test_subject.adapters.astrbot.admin_pages.bounded_body", body):
            left, right = await asyncio.gather(
                call(requests["left"][0]), call(requests["right"][0])
            )
        self.assertEqual((left.status_code, right.status_code), (403, 200))
        self.assertEqual(
            (await self.effects("left"), await self.effects("right")), (0, 1)
        )
        self.assertEqual(self.source._proofs, {})
        self.assertEqual(self.page.requests, {})

    async def test_wrapped_coordinator_error_keeps_authorization_denial_and_dbzero(
        self,
    ):
        response, effects, _, entered = await self.scenario(
            "update", "verifier", wrapped=True
        )
        self.assertEqual((response.status_code, effects, entered), (403, 0, True))

    async def test_authorized_cas_conflict_retains_conflict_status_without_effect(self):
        response, effects, _, entered = await self.scenario("update", "cas")
        self.assertEqual((response.status_code, effects, entered), (409, 0, False))

    async def test_lifecycle_loss_rolls_back(self):
        response, effects, _, entered = await self.scenario("update", "lifecycle")
        self.assertEqual((response.status_code, effects, entered), (403, 0, True))

    async def test_source_close_rolls_back(self):
        response, effects, _, entered = await self.scenario("update", "source_close")
        self.assertEqual((response.status_code, effects, entered), (403, 0, True))

    async def test_page_close_rolls_back(self):
        response, effects, _, entered = await self.scenario("update", "page_close")
        self.assertEqual((response.status_code, effects, entered), (403, 0, True))

    async def test_revocation_after_valid_commit_denies_late_response_without_claiming_rollback(
        self,
    ):
        response, effects, _, entered = await self.scenario(
            "update", "verifier", "result"
        )
        self.assertEqual((response.status_code, effects, entered), (403, 1, True))

    async def test_revocation_at_final_cleanup_await_denies_late_response(self):
        response, effects, _, entered = await self.scenario(
            "update", "verifier", "cleanup"
        )
        self.assertEqual((response.status_code, effects, entered), (403, 1, True))

    async def test_fixed_original_deadline_is_not_extended_by_reverification(self):
        from ygl_test_subject.adapters.astrbot.admin_pages import _DashboardRequestFence

        request, raw, host = self.request("update")
        now = time.time()
        fence = _DashboardRequestFence(
            request, request.path, raw, host.subject, now + 1
        )
        with patch(
            "ygl_test_subject.adapters.astrbot.admin_pages.time", return_value=now + 2
        ):
            self.assertFalse(fence.current(raw))

    async def test_pending_cancellation_rolls_back_and_preserves_cancel(self):
        request, raw, host = self.request("update")
        entered, release = threading.Event(), threading.Event()

        async def body(_):
            return {}

        async def invoke(endpoint, core, data, context):
            def write(unit):
                unit.execute("INSERT INTO effects VALUES('cancelled')")
                entered.set()
                release.wait(3)
                unit.add_commit_guard(
                    lambda: self.source.fence(
                        context, AdminOperation.UPDATE_CONFIG, self.resources
                    )
                )

            await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")
            return {}

        self.page._invoke = invoke
        token = self.request_context.set(request)
        try:
            with patch(
                "ygl_test_subject.adapters.astrbot.admin_pages.bounded_body", body
            ):
                task = asyncio.create_task(self.page._handler("update")())
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        finally:
            release.set()
            self.request_context.reset(token)
        self.assertEqual(await self.effects("cancelled"), 0)
        self.assertEqual(self.source._proofs, {})

    async def test_failed_issue_and_cancel_remove_only_the_new_proof(self):
        arguments = dict(
            subject="synthetic",
            request=object(),
            expiry=time.time() + 30,
            operations={AdminOperation.UPDATE_CONFIG},
            resources=self.resources,
        )
        existing = self.source.issue(**arguments, live=lambda raw: True)
        for _ in range(64):
            with self.assertRaises(AdminAuthorizationDenied):
                self.source.issue(**arguments, live=lambda raw: False)
            self.assertEqual(len(self.source._proofs), 1)
            self.source.check(existing, AdminOperation.UPDATE_CONFIG, self.resources)

        def cancelled(_raw):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            self.source.issue(**arguments, live=cancelled)
        self.assertEqual(len(self.source._proofs), 1)
        self.source.check(existing, AdminOperation.UPDATE_CONFIG, self.resources)
        self.source.close()
        self.assertEqual(self.source._proofs, {})

    async def test_pinned_host_verifier_and_plugin_request_normal_global_commit(self):
        from starlette.requests import Request

        from tests.host.astrbot_contract import host_contracts
        from tests.host.test_ff14_pages import _token

        web, namespace = host_contracts()
        host = namespace["HostAuth"]()
        host._jwt_secret = "synthetic-test-secret-not-host-at-least-32-bytes"
        path = f"/api/plug/{PLUGIN_NAME}/admin/update"

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        raw = Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "scheme": "https",
                "server": ("ui.test", 443),
                "query_string": b"",
                "headers": [
                    (b"authorization", ("Bearer " + _token()).encode()),
                    (b"origin", b"https://ui.test"),
                ],
                "app": N(state=N(dashboard_app_adapter=N(_dashboard_server=host))),
            },
            receive=receive,
        )
        raw.state.dashboard_g = N(username="synthetic")
        raw.app.state.dashboard_app_adapter._app = raw.app
        concrete = web.PluginRequest(raw, username="synthetic")

        async def body(_):
            await asyncio.sleep(0)
            return {}

        async def invoke(endpoint, core, data, context):
            def write(unit):
                unit.execute("INSERT INTO effects VALUES('actual-host-contract')")
                unit.add_commit_guard(
                    lambda: self.source.fence(
                        context, AdminOperation.UPDATE_CONFIG, self.resources
                    )
                )

            await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")
            return {"synthetic_effect": True}

        self.page._invoke = invoke
        with (
            patch.dict("sys.modules", {"astrbot.api.web": web}),
            patch("ygl_test_subject.adapters.astrbot.admin_pages.bounded_body", body),
            web.bind_request_context(concrete),
        ):
            response = await self.page._handler("update")()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(await self.effects("actual-host-contract"), 1)
        self.assertEqual(self.source._proofs, {})

    def failing_watcher_request(self, factory, *, revoke_on_join=False):
        def request(endpoint):
            concrete, raw, host = factory(endpoint)
            calls = 0

            async def disconnected():
                nonlocal calls
                calls += 1
                if calls == 1:
                    return False
                if revoke_on_join:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        host.valid = False
                        raise RuntimeError("SYNTHETIC RECEIVE FAILURE") from None
                raise RuntimeError("SYNTHETIC RECEIVE FAILURE")

            raw.is_disconnected = disconnected
            return concrete, raw, host

        return request

    def assert_request_resources_released(self):
        self.assertEqual(self.source._proofs, {})
        self.assertEqual(self.page.requests, {})
        self.assertEqual(
            [
                task
                for task in asyncio.all_tasks()
                if task.get_name() == "ygl:admin-disconnect"
            ],
            [],
        )

    async def test_repeated_watcher_failures_return_safe_503_without_retaining_requests(
        self,
    ):
        factory = self.failing_watcher_request(self.request)
        with patch.object(self, "request", factory):
            for repetition in range(2):
                for endpoint in (
                    "master-key-reset",
                    "legacy-config-execute",
                    "update",
                ):
                    with self.subTest(repetition=repetition, endpoint=endpoint):
                        response, effects, _, entered = await self.scenario(endpoint)
                        self.assertEqual(
                            (response.status_code, effects, entered), (503, 1, True)
                        )
                        self.assertEqual(
                            response.payload["code"], "operation_unavailable"
                        )
                        self.assert_request_resources_released()

    async def test_primary_cas_error_survives_watcher_failure_and_releases_requests(
        self,
    ):
        factory = self.failing_watcher_request(self.request)
        with patch.object(self, "request", factory):
            response, effects, _, entered = await self.scenario("update", "cas")
        self.assertEqual((response.status_code, effects, entered), (409, 0, False))
        self.assertEqual(response.payload["code"], "revision_conflict")
        self.assert_request_resources_released()

    async def test_watcher_failure_does_not_override_commit_or_result_auth_denial(self):
        factory = self.failing_watcher_request(self.request)
        with patch.object(self, "request", factory):
            for endpoint in ("master-key-reset", "legacy-config-execute", "update"):
                for phase, expected_effects in (("commit", 0), ("result", 1)):
                    with self.subTest(endpoint=endpoint, phase=phase):
                        response, effects, _, entered = await self.scenario(
                            endpoint, "verifier", phase
                        )
                        self.assertEqual(
                            (response.status_code, effects, entered),
                            (403, expected_effects, True),
                        )
                        self.assertEqual(
                            response.payload["code"], "admin_authorization_denied"
                        )
                        self.assert_request_resources_released()

    async def test_watcher_failure_at_final_join_keeps_late_auth_denial_priority(self):
        factory = self.failing_watcher_request(self.request, revoke_on_join=True)
        with patch.object(self, "request", factory):
            for endpoint in ("master-key-reset", "legacy-config-execute", "update"):
                with self.subTest(endpoint=endpoint):
                    response, effects, _, entered = await self.scenario(endpoint)
                    self.assertEqual(
                        (response.status_code, effects, entered), (403, 1, True)
                    )
                    self.assertEqual(
                        response.payload["code"], "admin_authorization_denied"
                    )
                    self.assert_request_resources_released()

    async def test_pending_sql_cancellation_with_failed_watcher_releases_requests(self):
        factory = self.failing_watcher_request(self.request)
        with patch.object(self, "request", factory):
            await self.test_pending_cancellation_rolls_back_and_preserves_cancel()
        self.assert_request_resources_released()

    async def test_external_cancellation_during_watcher_join_releases_requests(self):
        for repeated_cancel in (False, True):
            with self.subTest(repeated_cancel=repeated_cancel):
                request, raw, _host = self.request("update")
                joining, second_cancel = asyncio.Event(), asyncio.Event()
                release = asyncio.Event()
                calls, cancellations = 0, 0

                async def disconnected():
                    nonlocal calls, cancellations
                    calls += 1
                    if calls == 1:
                        return False
                    while True:
                        try:
                            await release.wait()
                            return False
                        except asyncio.CancelledError:
                            cancellations += 1
                            if cancellations == 1:
                                joining.set()
                            elif cancellations == 2 and repeated_cancel:
                                second_cancel.set()
                            else:
                                raise

                raw.is_disconnected = disconnected
                label = "cancel-at-join-" + str(repeated_cancel)

                async def body(_):
                    return {}

                async def invoke(endpoint, core, data, context):
                    def write(unit):
                        unit.execute("INSERT INTO effects VALUES(?)", (label,))
                        unit.add_commit_guard(
                            lambda: self.source.fence(
                                context, AdminOperation.UPDATE_CONFIG, self.resources
                            )
                        )

                    await self.database.executor.run_transaction(
                        write, begin_mode="IMMEDIATE"
                    )
                    return {"synthetic_effect": True}

                self.page._invoke = invoke
                token = self.request_context.set(request)
                task = None
                try:
                    with patch(
                        "ygl_test_subject.adapters.astrbot.admin_pages.bounded_body",
                        body,
                    ):
                        task = asyncio.create_task(self.page._handler("update")())
                        await asyncio.wait_for(joining.wait(), 3)
                        task.cancel()
                        if repeated_cancel:
                            await asyncio.wait_for(second_cancel.wait(), 3)
                            task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 3)
                finally:
                    release.set()
                    self.request_context.reset(token)
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                self.assertTrue(task.cancelled())
                self.assertEqual(await self.effects(label), 1)
                self.assertEqual(cancellations, 3 if repeated_cancel else 2)
                self.assert_request_resources_released()

    async def test_real_starlette_swallowed_watcher_cancel_stops_and_keeps_fresh_fence(
        self,
    ):
        from starlette.requests import Request

        for revoke_at_cleanup in (False, True):
            with self.subTest(revoke_at_cleanup=revoke_at_cleanup):
                concrete, fake_raw, host = self.request("update")
                disconnect_event = asyncio.Event()
                resumed_after_cancel = asyncio.Event()
                watchers, observations = [], []
                receive_calls = 0
                label = "real-anyio-stop-" + str(revoke_at_cleanup)

                async def receive():
                    nonlocal receive_calls
                    current = asyncio.current_task()
                    if current.get_name() != "ygl:admin-disconnect":
                        return {
                            "type": "http.request",
                            "body": b"",
                            "more_body": False,
                        }
                    if not watchers:
                        watchers.append(current)
                    receive_calls += 1
                    if receive_calls == 1:
                        # Real Request.is_disconnected owns its AnyIO scope;
                        # the receiver does not catch or suppress cancellation.
                        asyncio.get_running_loop().call_soon(current.cancel)
                    else:
                        resumed_after_cancel.set()
                    await disconnect_event.wait()
                    return {"type": "http.disconnect"}

                raw = Request(
                    {
                        "type": "http",
                        "method": "POST",
                        "path": concrete.path,
                        "scheme": "https",
                        "server": ("ui.test", 443),
                        "query_string": b"",
                        "headers": [
                            (b"authorization", b"Bearer synthetic.token.value"),
                            (b"origin", b"https://ui.test"),
                        ],
                        "app": fake_raw.app,
                    },
                    receive=receive,
                )
                raw.state.dashboard_g = N(username=host.subject)
                concrete._request = raw

                async def body(_):
                    return {}

                async def invoke(endpoint, core, data, context):
                    await asyncio.wait_for(resumed_after_cancel.wait(), 3)
                    # The receiver signals inside the cancel scope. Let its
                    # queued cancellation exit/unwind before sampling the task.
                    await asyncio.sleep(0)
                    monitor = watchers[0]
                    observations.append(
                        (monitor.done(), monitor.cancelled(), monitor.cancelling())
                    )

                    def write(unit):
                        unit.execute("INSERT INTO effects VALUES(?)", (label,))
                        unit.add_commit_guard(
                            lambda: self.source.fence(
                                context, AdminOperation.UPDATE_CONFIG, self.resources
                            )
                        )

                    await self.database.executor.run_transaction(
                        write, begin_mode="IMMEDIATE"
                    )
                    if revoke_at_cleanup:
                        asyncio.get_running_loop().call_soon(
                            setattr, host, "valid", False
                        )
                    return {"synthetic_effect": True}

                async def call():
                    token = self.request_context.set(concrete)
                    try:
                        return await self.page._handler("update")()
                    finally:
                        self.request_context.reset(token)

                self.page._invoke = invoke
                handler = None
                with patch(
                    "ygl_test_subject.adapters.astrbot.admin_pages.bounded_body", body
                ):
                    try:
                        handler = asyncio.create_task(call())
                        done, _ = await asyncio.wait({handler}, timeout=3)
                        self.assertIn(handler, done)
                        response = await handler
                        self.assertEqual(observations, [(False, False, 1)])
                        self.assertEqual(
                            response.status_code, 403 if revoke_at_cleanup else 200
                        )
                        self.assertEqual(await self.effects(label), 1)
                        self.assert_request_resources_released()
                    finally:
                        # Every assertion/exception path releases the actual
                        # receiver and joins all owned tasks before loop shutdown.
                        disconnect_event.set()
                        owned = [*watchers]
                        if handler is not None:
                            owned.append(handler)
                        if owned:
                            done, pending = await asyncio.wait(owned, timeout=3)
                            for pending_task in pending:
                                pending_task.cancel()
                            await asyncio.gather(*owned, return_exceptions=True)
                        self.assert_request_resources_released()
