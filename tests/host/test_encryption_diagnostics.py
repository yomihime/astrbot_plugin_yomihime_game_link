"""Private opt-in diagnostics through real Host auth with isolated synthetic data."""

import asyncio
import time
import unittest
from unittest.mock import Mock, patch

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.infrastructure.key_provider import (
    EnvironmentKeyProvider,
    SecretKeyUnavailable,
)

from tests.host import test_admin_pages as admin_fixture
from tests.host.test_ff14_pages import _token


class EncryptionDiagnosticsHostTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = admin_fixture.AdminPagesTests.asyncSetUp
    asyncTearDown = admin_fixture.AdminPagesTests.asyncTearDown
    call = admin_fixture.AdminPagesTests.call

    def issue(self, **overrides):
        ops = self.runtime.core_runtime.admin_operations
        arguments = dict(
            subject="synthetic",
            request=object(),
            expiry=time.time() + 5,
            operations={AdminOperation.READ_CONFIG},
            resources=ops.credential_resources(),
            live=lambda _: True,
        )
        arguments.update(overrides)
        return self.runtime._credential_admin_source.issue(**arguments)

    async def test_opt_in_only_preserves_default_and_returns_safe_categories(self):
        with patch.object(
            EnvironmentKeyProvider, "get_key", return_value=bytes(range(32))
        ) as provider:
            ordinary = await self.call("credential-readiness", {})
            self.assertEqual(ordinary.status_code, 200)
            self.assertEqual(
                ordinary.json()["data"],
                {"ready": True, "state": "ready", "reason_code": "ready"},
            )
            provider.reset_mock()
            response = await self.call("credential-readiness", {"diagnostics": True})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(
                set(response.json()["data"]),
                {"ready", "state", "reason_code", "diagnostics"},
            )
            self.assertEqual(
                response.json()["data"]["diagnostics"],
                {"key_state": "valid_format", "codec_state": "available"},
            )
            provider.assert_called_once()
        for index, (error, expected) in enumerate(
            [
                (SecretKeyUnavailable(reason="not_provided"), "not_provided"),
                (
                    SecretKeyUnavailable(reason="invalid_encoding_or_length"),
                    "invalid_encoding_or_length",
                ),
                (RuntimeError("PRIVATE-SYNTHETIC"), "cannot_safely_confirm"),
            ]
        ):
            with (
                self.subTest(case=index),
                patch.object(
                    EnvironmentKeyProvider, "get_key", side_effect=error
                ) as provider,
            ):
                response = await self.call(
                    "credential-readiness", {"diagnostics": True}
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertIs(data["ready"], False)
                self.assertEqual(data["reason_code"], "secret_encryption_unavailable")
                self.assertEqual(
                    data["diagnostics"],
                    {"key_state": expected, "codec_state": "not_run"},
                )
                self.assertNotIn("PRIVATE-SYNTHETIC", response.text)
                provider.assert_called_once()
        self.assertEqual(self.runtime._credential_admin_source._proofs, {})

    async def test_bad_body_and_identity_origin_denials_never_read_provider(self):
        with patch.object(
            EnvironmentKeyProvider,
            "get_key",
            side_effect=AssertionError("must not read"),
        ) as provider:
            bodies = [
                [],
                None,
                True,
                1,
                "true",
                {"diagnostics": False},
                {"diagnostics": 1},
                {"diagnostics": None},
                {"diagnostics": "true"},
                {"diagnostics": True, "extra": True},
                {"role": "admin"},
            ]
            for index, body in enumerate(bodies):
                with self.subTest(body=index):
                    self.assertEqual(
                        (await self.call("credential-readiness", body)).status_code, 400
                    )
            headers = [
                ({}, 401),
                ({**self.headers, "Origin": "https://other.test"}, 403),
                (
                    {
                        "X-API-Key": "synthetic-active-api-key",
                        "Origin": "https://ui.test",
                    },
                    401,
                ),
                ({**self.headers, "X-API-Key": "synthetic-active-api-key"}, 403),
                (
                    {
                        **self.headers,
                        "Authorization": "Bearer "
                        + _token(
                            token_type="plugin_page_asset",
                            plugin_name="astrbot_plugin_yomihime_game_link",
                            page_name="management",
                        ),
                    },
                    401,
                ),
            ]
            for index, (header, status) in enumerate(headers):
                with self.subTest(identity=index):
                    self.assertEqual(
                        (
                            await self.call(
                                "credential-readiness",
                                {"diagnostics": True},
                                headers=header,
                            )
                        ).status_code,
                        status,
                    )
            provider.assert_not_called()
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._credential_admin_source._proofs, {})

    async def test_wrong_resource_expired_and_revoked_proofs_never_probe(self):
        ops = self.runtime.core_runtime.admin_operations
        source = self.runtime._credential_admin_source
        with patch.object(EnvironmentKeyProvider, "get_key") as provider:
            with self.assertRaises(AdminAuthorizationDenied):
                self.issue(resources=ops.ordinary_resources())
            context = self.issue()
            try:
                with patch(
                    "ygl_test_subject.services.admin_sources.time",
                    return_value=time.time() + 120,
                ):
                    with self.assertRaises(AdminAuthorizationDenied):
                        await ops.credential_readiness(
                            authorization=context, diagnostics=True
                        )
            finally:
                source.end(context)
            provider.assert_not_called()
        context = self.issue()
        source.end(context)
        with patch.object(EnvironmentKeyProvider, "get_key") as provider:
            with self.assertRaises(AdminAuthorizationDenied):
                await ops.credential_readiness(authorization=context, diagnostics=True)
            provider.assert_not_called()

    async def test_revoked_while_waiting_admission_never_probes(self):
        core = self.runtime.core_runtime
        ops, source = core.admin_operations, self.runtime._credential_admin_source
        context, authorized = self.issue(), asyncio.Event()
        authorize = ops.authorization.authorize

        async def observe(*args, **kwargs):
            grant = await authorize(*args, **kwargs)
            authorized.set()
            return grant

        lock = core.lifecycle.admission._mutation_lock
        await lock.acquire()
        try:
            with (
                patch.object(ops.authorization, "authorize", observe),
                patch.object(EnvironmentKeyProvider, "get_key") as provider,
            ):
                task = asyncio.create_task(
                    ops.credential_readiness(authorization=context, diagnostics=True)
                )
                await asyncio.wait_for(authorized.wait(), 2)
                source.end(context)
                lock.release()
                with self.assertRaises(AdminAuthorizationDenied):
                    await task
                provider.assert_not_called()
        finally:
            source.end(context)
            if lock.locked():
                lock.release()

    async def test_post_probe_revocation_and_detached_host_discard_diagnostics(self):
        ops, source = (
            self.runtime.core_runtime.admin_operations,
            self.runtime._credential_admin_source,
        )
        context = self.issue()

        def revoke():
            source.end(context)
            return bytes(range(32))

        with patch.object(
            EnvironmentKeyProvider, "get_key", side_effect=revoke
        ) as provider:
            with self.assertRaises(AdminAuthorizationDenied):
                await ops.credential_readiness(authorization=context, diagnostics=True)
            provider.assert_called_once()

        def detach():
            self.pages.close()
            return bytes(range(32))

        with patch.object(
            EnvironmentKeyProvider, "get_key", side_effect=detach
        ) as provider:
            response = await self.call("credential-readiness", {"diagnostics": True})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("diagnostics", response.text)
            provider.assert_called_once()

    async def test_internal_nonboolean_flag_fails_before_provider(self):
        ops, source = (
            self.runtime.core_runtime.admin_operations,
            self.runtime._credential_admin_source,
        )
        context = self.issue()
        try:
            with patch.object(EnvironmentKeyProvider, "get_key", Mock()) as provider:
                for value in [1, None, "true", {}]:
                    with self.assertRaises(ValueError):
                        await ops.credential_readiness(
                            authorization=context, diagnostics=value
                        )
                provider.assert_not_called()
        finally:
            source.end(context)

    async def test_cancelled_admission_wait_never_probes_or_retains_request(self):
        core = self.runtime.core_runtime
        ops, source = core.admin_operations, self.runtime._credential_admin_source
        authorized = asyncio.Event()
        authorize = ops.authorization.authorize

        async def observe(*args, **kwargs):
            grant = await authorize(*args, **kwargs)
            authorized.set()
            return grant

        async def request():
            context = self.issue()
            try:
                return await ops.credential_readiness(
                    authorization=context, diagnostics=True
                )
            finally:
                source.end(context)

        lock = core.lifecycle.admission._mutation_lock
        await lock.acquire()
        try:
            with (
                patch.object(ops.authorization, "authorize", observe),
                patch.object(EnvironmentKeyProvider, "get_key") as provider,
            ):
                task = asyncio.create_task(request())
                await asyncio.wait_for(authorized.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                provider.assert_not_called()
                self.assertEqual(source._proofs, {})
        finally:
            lock.release()
