"""Pinned real Host ASGI auth/dispatch, synthetic JWT/storage and page grants."""

import asyncio
import os
import shutil
import time
import unittest
import uuid
from pathlib import Path
from types import MethodType
from unittest.mock import patch

from ygl_test_subject.infrastructure.private_storage import resolve_private_storage
from ygl_test_subject.infrastructure.windows.private_storage import (
    create_private_directory,
    identity_private_base,
)

from tests.host import test_admin_pages as harness


@unittest.skipUnless(os.name == "nt", "native private synthetic Host data boundary")
class ModulePageHTTPTests(unittest.IsolatedAsyncioTestCase):
    call = harness.AdminPagesTests.call

    async def asyncSetUp(self):
        self.owned = create_private_directory(
            identity_private_base(), "ygl-page-http-" + uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, self.owned)
        self.fixture_parent = self.owned
        self.env = patch.dict(os.environ, {"YGL_SECRET_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.layout_patch = patch(
            "ygl_test_subject.adapters.astrbot.runtime.resolve_private_storage",
            lambda data: resolve_private_storage(data, private_base=self.owned),
        )
        self.layout_patch.start()
        self.addCleanup(self.layout_patch.stop)
        # A targeted build may live outside formal output paths while authors
        # are still working. Overlay only its compiled current module assets
        # into this test's private source copy, never into the checkout.
        staging = os.environ.get("YGL_TEST_FRONTEND_STAGING")
        if staging:
            original_copytree = shutil.copytree
            source_module = Path(__file__).resolve().parents[2] / "modules/ff14"

            def copied(source, destination, *args, **kwargs):
                result = original_copytree(source, destination, *args, **kwargs)
                if Path(source).resolve() == source_module:
                    staged = Path(staging) / "modules/ff14"
                    original_copytree(
                        staged / "pages/dist", Path(destination) / "pages/dist"
                    )
                    shutil.copyfile(
                        staged / "yomihime.manifest.json",
                        Path(destination) / "yomihime.manifest.json",
                    )
                return result

            copying = patch.object(shutil, "copytree", copied)
            copying.start()
            self.addCleanup(copying.stop)
        await harness.AdminPagesTests.asyncSetUp(self)
        module = next(
            item
            for item in self.runtime.public_module_catalog()["modules"]
            if item["module_id"] == "ff14/ff14"
        )
        self.binding = dict(
            owner=module["module_id"],
            page="config",
            runtime_id=module["runtime_id"],
            module_epoch=module["module_epoch"],
        )

    async def asyncTearDown(self):
        try:
            self.assertEqual(self.pages.requests, {})
            for name in (
                "_admin_source",
                "_credential_admin_source",
                "_module_admin_source",
            ):
                source = getattr(self.runtime, name, None)
                if source is not None:
                    self.assertEqual(source._proofs, {})
            self.assertFalse(
                any(
                    task.get_name() == "ygl:module-page-disconnect"
                    for task in asyncio.all_tasks()
                )
            )
        finally:
            await harness.AdminPagesTests.asyncTearDown(self)

    async def session(self):
        response = await self.call("module-page-session", self.binding)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["page_session"]

    async def test_actual_host_session_bootstrap_read_write_and_close(self):
        proof = await self.session()
        read = await self.call(
            "module-config-current", self.binding | {"page_session": proof}
        )
        self.assertEqual(read.status_code, 200, read.text)
        snapshot = read.json()["data"]
        self.assertIn("reply_theme", snapshot["fields"])
        saved = await self.call(
            "module-config-save",
            self.binding
            | {
                "page_session": proof,
                "expected_revision": snapshot["revision"],
                "values": {"ff14_calendar_default_days": 8},
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        closed = await self.call("module-page-session-close", {"page_session": proof})
        self.assertEqual(closed.status_code, 200, closed.text)
        denied = await self.call(
            "module-config-current", self.binding | {"page_session": proof}
        )
        self.assertEqual(denied.status_code, 403, denied.text)

    async def test_actual_host_verifier_revoked_after_sql_before_commit_rolls_back(
        self,
    ):
        proof = await self.session()
        before = await self.call(
            "module-config-current", self.binding | {"page_session": proof}
        )
        snapshot = before.json()["data"]
        valid = [True]
        entered_write = [False]
        verify = self.auth._validate_dashboard_token

        def verifier(server, token, path):
            return verify(token, path) if valid[0] else (None, "synthetic-revoked")

        repository = self.runtime.core_runtime.config_repository
        original = repository._update_in_unit

        def update(unit, target, config_patch):
            result = original(unit, target, config_patch)
            entered_write[0] = True
            valid[0] = False
            return result

        with (
            patch.object(
                self.auth, "_validate_dashboard_token", MethodType(verifier, self.auth)
            ),
            patch.object(repository, "_update_in_unit", update),
        ):
            response = await self.call(
                "module-config-save",
                self.binding
                | {
                    "page_session": proof,
                    "expected_revision": snapshot["revision"],
                    "values": {"ff14_calendar_default_days": 9},
                },
            )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertTrue(
            entered_write[0], "the real SQLite write must be entered before revocation"
        )
        after = await self.call(
            "module-config-current", self.binding | {"page_session": proof}
        )
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()["data"]["revision"], snapshot["revision"])
        self.assertEqual(
            after.json()["data"]["fields"]["ff14_calendar_default_days"]["value"],
            snapshot["fields"]["ff14_calendar_default_days"]["value"],
        )

    async def test_outer_module_result_rechecks_host_authority_for_every_action(self):
        # Actual Host JWT/ASGI dispatch; the operation is inert to isolate the
        # outer HTTP result fence without minting sessions or repeating writes.
        valid, calls = [True], []
        verify = self.auth._validate_dashboard_token

        def verifier(server, token, path):
            return verify(token, path) if valid[0] else (None, "synthetic-revoked")

        async def operation(server, action, data, *, request):
            calls.append(action)
            await asyncio.sleep(0)
            valid[0] = False
            return {"synthetic": True}

        with (
            patch.object(
                self.auth, "_validate_dashboard_token", MethodType(verifier, self.auth)
            ),
            patch.object(
                self.runtime,
                "module_page_operation",
                MethodType(operation, self.runtime),
            ),
        ):
            for action in (
                "module-page-session",
                "module-page-session-close",
                "module-config-current",
                "module-config-save",
                "module-config-clear",
                "module-credential-status",
                "module-credential-replace",
                "module-credential-clear",
            ):
                with self.subTest(action=action):
                    valid[0] = True
                    before = len(calls)
                    response = await self.call(action, {})
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertEqual(
                        response.json()["code"], "admin_authorization_denied"
                    )
                    self.assertEqual(calls[before:], [action])

    async def test_outer_module_result_keeps_original_expiry_and_host_identity(self):
        from starlette.datastructures import Headers

        from tests.host.test_ff14_pages import _token

        original_verifier = self.auth._validate_dashboard_token
        generation = self.runtime._generation
        for change in ("expiry", "verifier", "origin", "bearer", "generation"):
            with self.subTest(change=change):
                clock, calls = [time.time()], []

                async def operation(server, action, data, *, request):
                    calls.append(action)
                    await asyncio.sleep(0)
                    if change == "expiry":
                        clock[0] += 31
                    elif change == "verifier":

                        def changed(server, token, path):
                            return original_verifier(token, path)

                        self.auth._validate_dashboard_token = MethodType(
                            changed, self.auth
                        )
                    elif change == "generation":
                        self.runtime._generation += 1
                    else:
                        headers = dict(request.headers)
                        previous = request.headers
                        if change == "origin":
                            headers["origin"] = "https://different.test"
                        else:
                            headers["authorization"] = "Bearer " + _token(
                                exp=int(time.time()) + 120
                            )
                        request.headers = request._request._headers = Headers(headers)
                        self.assertNotEqual(
                            request.headers.getlist(
                                "origin" if change == "origin" else "authorization"
                            ),
                            previous.getlist(
                                "origin" if change == "origin" else "authorization"
                            ),
                        )
                    return {"synthetic": True}

                try:
                    with (
                        patch(
                            "ygl_test_subject.adapters.astrbot.admin_pages.time",
                            lambda: clock[0],
                        ),
                        patch.object(
                            self.runtime,
                            "module_page_operation",
                            MethodType(operation, self.runtime),
                        ),
                    ):
                        response = await self.call("module-config-current", {})
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertEqual(calls, ["module-config-current"])
                finally:
                    self.auth._validate_dashboard_token = original_verifier
                    self.runtime._generation = generation
