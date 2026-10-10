"""Synthetic Dashboard credential management, exact grants and encrypted CAS."""

import asyncio
import base64
import json
import os
import time
import unittest
from unittest.mock import patch

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract

from tests.host import test_admin_pages as admin_fixture
from yomihime_game_link_sdk.services import ConfigTarget


class ManagedCredentialPagesTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = admin_fixture.AdminPagesTests.asyncSetUp
    asyncTearDown = admin_fixture.AdminPagesTests.asyncTearDown
    call = admin_fixture.AdminPagesTests.call

    async def status(self):
        response = await self.call("credential-status", {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    async def change(self, field, mode="replace", revision=None, value=None):
        if revision is None:
            revision = (await self.status())["ff14/ff14"]["revision"]
        update = {"field": field, "mode": mode}
        if mode == "replace":
            update["value"] = (
                value
                if value is not None
                else {
                    "client_id": "synthetic-client",
                    "client_secret": "synthetic-private-pair",
                }
            )
        return await self.call(
            "credential-update",
            {
                "module_id": "ff14/ff14",
                "expected_revision": revision,
                "updates": [update],
            },
        )

    async def test_exact_separate_issuer_no_rollback_recover_or_ordinary_write(self):
        source = self.runtime._credential_admin_source
        self.assertIsNot(source, self.runtime._admin_source)
        target = validate_contract(
            ConfigTarget("astrbot_plugin_yomihime_game_link", "ff14/ff14")
        )
        for operation in (
            AdminOperation.ROLLBACK_CONFIG,
            AdminOperation.RECOVER_CONFIG,
        ):
            with self.assertRaises(AdminAuthorizationDenied):
                source.issue(
                    subject="synthetic",
                    request=object(),
                    expiry=time.time() + 5,
                    operations={operation},
                    resources={target: {"credential_fflogs_cn"}},
                    live=lambda: True,
                )
        for alias in ("ff14_calendar_default_days", "credential_other"):
            with self.assertRaises(AdminAuthorizationDenied):
                source.issue(
                    subject="synthetic",
                    request=object(),
                    expiry=time.time() + 5,
                    operations={AdminOperation.UPDATE_CONFIG},
                    resources={target: {alias}},
                    live=lambda: True,
                )
        response = await self.call(
            "update",
            {
                "module_id": "ff14/ff14",
                "expected_revision": 1,
                "updates": [{"field": "credential_fflogs_cn", "mode": "clear"}],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(source._proofs, {})

    async def test_proof_identity_lifetime_live_and_owned_source_revocation(self):
        from dataclasses import replace

        source = self.runtime._credential_admin_source
        target = validate_contract(
            ConfigTarget("astrbot_plugin_yomihime_game_link", "ff14/ff14")
        )
        resources = {target: {"credential_fflogs_cn"}}
        alive = [True]
        context = source.issue(
            subject="synthetic",
            request=object(),
            expiry=time.time() + 5,
            operations={AdminOperation.READ_CONFIG},
            resources=resources,
            live=lambda _request: alive[0],
        )
        source.check(context, AdminOperation.READ_CONFIG, resources)
        for proof in (replace(context), None):
            with self.assertRaises(AdminAuthorizationDenied):
                source.check(proof, AdminOperation.READ_CONFIG, resources)
        with self.assertRaises(AdminAuthorizationDenied):
            source.check(
                context,
                AdminOperation.READ_CONFIG,
                {
                    validate_contract(ConfigTarget(target.principal_id, "other/mod")): {
                        "credential_fflogs_cn"
                    }
                },
            )
        alive[0] = False
        with self.assertRaises(AdminAuthorizationDenied):
            source.check(context, AdminOperation.READ_CONFIG, resources)
        source.end(context)
        with self.assertRaises(AdminAuthorizationDenied):
            source.issue(
                subject="synthetic",
                request=object(),
                expiry=time.time() - 1,
                operations={AdminOperation.READ_CONFIG},
                resources=resources,
                live=lambda _: True,
            )

        source.close()
        self.assertFalse(self.runtime._admin_source._closed)
        with self.assertRaises(AdminAuthorizationDenied):
            source.issue(
                subject="synthetic",
                request=object(),
                expiry=time.time() + 5,
                operations={AdminOperation.READ_CONFIG},
                resources=resources,
                live=lambda _: True,
            )

    async def test_revoked_proof_waiting_admission_reads_no_credential_metadata(self):
        from unittest.mock import AsyncMock

        core = self.runtime.core_runtime
        ops = core.admin_operations
        source = self.runtime._credential_admin_source
        for method in (ops.credential_catalog, ops.credential_status):
            with self.subTest(method=method.__name__):
                issued, authorized = {}, asyncio.Event()
                authorize = ops.authorization.authorize

                async def observe(*args, **kwargs):
                    grant = await authorize(*args, **kwargs)
                    authorized.set()
                    return grant

                async def request():
                    context = source.issue(
                        subject="synthetic",
                        request=object(),
                        expiry=time.time() + 5,
                        operations={AdminOperation.READ_CONFIG},
                        resources=ops.credential_resources(),
                        live=lambda _: True,
                    )
                    issued["context"] = context
                    try:
                        return await method(authorization=context)
                    finally:
                        source.end(context)

                lock = core.lifecycle.admission._mutation_lock
                await lock.acquire()
                spy = AsyncMock(wraps=core.config_repository.current)
                try:
                    with (
                        patch.object(ops.authorization, "authorize", observe),
                        patch.object(core.config_repository, "current", spy),
                        patch.object(
                            ops,
                            "_credential_selections",
                            wraps=ops._credential_selections,
                        ) as selection,
                    ):
                        task = asyncio.create_task(request())
                        await asyncio.wait_for(authorized.wait(), 2)
                        source.end(issued["context"])
                        lock.release()
                        with self.assertRaises(AdminAuthorizationDenied):
                            await task
                        self.assertEqual(spy.call_count, 0)
                        self.assertEqual(selection.call_count, 0)
                finally:
                    if lock.locked():
                        lock.release()

    async def test_concurrent_same_revision_only_one_commit_and_disconnect_cancels_staged(
        self,
    ):
        with patch.dict(
            os.environ, {"YGL_SECRET_KEY": base64.b64encode(b"s" * 32).decode()}
        ):
            revision = (await self.status())["ff14/ff14"]["revision"]
            responses = await asyncio.gather(
                self.change("credential_fflogs_cn", revision=revision),
                self.change("credential_fflogs_global", revision=revision),
            )
            self.assertEqual(
                sorted(response.status_code for response in responses), [200, 409]
            )
            before = await self.status()
            started, release = asyncio.Event(), asyncio.Event()
            store = self.runtime.core_runtime.secret_store
            stage = store.stage

            async def gated(*args, **kwargs):
                receipt = await stage(*args, **kwargs)
                started.set()
                await release.wait()
                return receipt

            with patch.object(store, "stage", gated):
                task = asyncio.create_task(
                    self.change(
                        "credential_fflogs_cn", revision=before["ff14/ff14"]["revision"]
                    )
                )
                await asyncio.wait_for(started.wait(), 2)
                self.pages.close()
                release.set()
                response = await task
            self.assertEqual(response.status_code, 403, response.text)
            self.pages.register()
            self.assertEqual(await self.status(), before)
            self.assertEqual(self.runtime._credential_admin_source._proofs, {})

    async def test_unmanaged_default_and_sensitive_sdk_schema_cannot_grant(self):
        import inspect

        from ygl_test_subject.services.admin_operations import AdminOperationsService
        from ygl_test_subject.services.managed_source_credentials import (
            ManagedSourceCredentialPolicy,
        )

        from yomihime_game_link_sdk.declarations import ConfigField

        self.assertEqual(
            inspect.signature(AdminOperationsService)
            .parameters["managed_source_credentials"]
            .default,
            (),
        )
        with self.assertRaises(ValueError):
            validate_contract(
                ConfigField(
                    "credential_other",
                    sensitive=True,
                    value_schema={"type": "object", "properties": {}},
                )
            )
        policy = ManagedSourceCredentialPolicy(
            "other/mod", "credential_other", "other", "Other"
        )
        with self.assertRaises(ValueError):
            policy.validate_declaration(
                self.runtime.core_runtime.registry.snapshot()
                .modules.get("ff14/ff14")
                .manifest
                if self.runtime.ready
                else self.runtime._assembly.manifests["ff14/ff14"]
            )
        response = await self.call(
            "credential-update",
            {
                "module_id": "other/mod",
                "expected_revision": 1,
                "updates": [{"field": "credential_other", "mode": "clear"}],
            },
        )
        self.assertEqual(response.status_code, 403, response.text)

    async def test_default_empty_management_allowlist_never_reads_declared_credentials(
        self,
    ):
        ops = self.runtime.core_runtime.admin_operations
        policies = ops._managed_source_credentials
        try:
            ops._managed_source_credentials = ()
            with patch.object(
                self.runtime.core_runtime.config_repository,
                "current",
                side_effect=AssertionError("no metadata grant"),
            ):
                for endpoint in ("credential-catalog", "credential-status"):
                    self.assertEqual((await self.call(endpoint, {})).status_code, 403)
        finally:
            ops._managed_source_credentials = policies

    async def test_metadata_only_and_two_realms_encrypted_replace_clear_cas(self):
        catalog = await self.call("credential-catalog", {})
        self.assertEqual(catalog.status_code, 200, catalog.text)
        fields = catalog.json()["data"]["fields"]
        self.assertEqual(
            {f["name"] for f in fields},
            {"credential_fflogs_cn", "credential_fflogs_global"},
        )
        self.assertTrue(
            all(f["value_schema"]["additionalProperties"] is False for f in fields)
        )
        self.assertNotIn("default", fields[0])
        with patch.dict(
            os.environ, {"YGL_SECRET_KEY": base64.b64encode(b"s" * 32).decode()}
        ):
            for alias in ("credential_fflogs_cn", "credential_fflogs_global"):
                revision = (await self.status())["ff14/ff14"]["revision"]
                saved = await self.change(alias, revision=revision)
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertNotIn("synthetic-private", saved.text)
                self.assertEqual(
                    (await self.change(alias, revision=revision)).status_code, 409
                )
                core = self.runtime.core_runtime
                with patch.object(
                    core.secret_store, "read", side_effect=AssertionError("no readback")
                ):
                    status = await self.status()
                self.assertEqual(status["ff14/ff14"]["fields"][alias], "configured")
                self.assertNotIn("synthetic-client", json.dumps(status))
                snapshot = await core.config_repository.current(
                    validate_contract(
                        ConfigTarget("astrbot_plugin_yomihime_game_link", "ff14/ff14")
                    )
                )
                self.assertNotIn(alias, snapshot.values)
                for secret in (self.runtime._data_dir / "secrets").rglob("*"):
                    if secret.is_file():
                        self.assertNotIn(b"synthetic-private-pair", secret.read_bytes())
                cleared = await self.change(alias, mode="clear")
                self.assertEqual(cleared.status_code, 200, cleared.text)
                self.assertEqual(
                    (await self.status())["ff14/ff14"]["fields"][alias], "unset"
                )

    async def test_closed_pair_missing_key_and_no_effect(self):
        before = await self.status()
        for value in (
            {},
            {"client_id": "x"},
            {"client_id": "x", "client_secret": ""},
            {"client_id": "x", "client_secret": "x", "extra": "x"},
            {"client_id": "x" * 513, "client_secret": "x"},
            {"client_id": "x", "client_secret": "x\n"},
            {"client_id": 1, "client_secret": "x"},
        ):
            response = await self.change("credential_fflogs_cn", value=value)
            self.assertEqual(response.status_code, 400, response.text)
        with patch.dict(os.environ, {}, clear=True):
            response = await self.change("credential_fflogs_cn")
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["code"], "secret_encryption_unavailable")
        self.assertEqual(await self.status(), before)

    async def test_credential_endpoints_retain_host_negative_identity_proofs(self):
        from tests.host.test_ff14_pages import _token

        bad = (
            {},
            {**self.headers, "Origin": "https://other.test"},
            {
                **self.headers,
                "Authorization": "Bearer "
                + _token(
                    token_type="plugin_page_asset",
                    plugin_name="astrbot_plugin_yomihime_game_link",
                    page_name="management",
                ),
            },
        )
        for endpoint in (
            "credential-catalog",
            "credential-status",
            "credential-update",
        ):
            for headers in bad:
                response = await self.call(endpoint, {}, headers=headers)
                self.assertNotEqual(response.status_code, 200)
        for endpoint in ("credential-catalog", "credential-status"):
            self.assertEqual(
                (await self.call(endpoint, {"role": "admin"})).status_code, 400
            )
        self.assertEqual(self.runtime._credential_admin_source._proofs, {})
