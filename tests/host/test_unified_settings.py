"""Generic management and public envelopes; only synthetic keys and isolated data."""

import base64
import json
import os
import unittest
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.web_public import (
    HostPublicWebValidator,
    WebPublicRejected,
    invocation_envelope,
)
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore

from tests.host import test_admin_pages as admin_fixture


class GenericEnvelopeTests(unittest.TestCase):
    def test_closed_bounded_envelope_and_same_local_page_are_owner_isolated(self):
        payload = {
            "owner": "example/demo",
            "page": "items",
            "capability_id": "lookup",
            "parameters": {"query": "safe"},
        }
        self.assertEqual(
            invocation_envelope(json.dumps(payload).encode()),
            ("example/demo", "items", "lookup", {"query": "safe"}),
        )
        for field in ("role", "endpoint", "grant"):
            with self.assertRaises(WebPublicRejected):
                invocation_envelope(json.dumps({**payload, field: "admin"}).encode())
        with self.assertRaises(WebPublicRejected):
            invocation_envelope(b'{"owner":"example/demo","owner":"other/demo"}')
        validator = HostPublicWebValidator(
            1,
            lambda *_: True,
            bindings={
                ("example/demo", "items", "lookup"): ("example/demo", "lookup"),
                ("other/demo", "items", "lookup"): ("other/demo", "lookup"),
            },
            clock=lambda: 0,
            wall_clock=lambda: 0,
        )
        validator.attach(object())
        ticket = validator.begin(("example/demo", "items", "lookup"), "synthetic", 100)
        proof = validator.mint(ticket)
        with self.assertRaises(WebPublicRejected):
            validator.consume(
                proof, module_id="other/demo", capability_id="lookup", generation=1
            )
        binding = validator.consume(
            proof, module_id="example/demo", capability_id="lookup", generation=1
        )
        self.assertTrue(validator.is_current(proof, binding))
        validator.revoke(proof)
        self.assertFalse(validator.is_current(proof, binding))

    def test_current_injected_codec_readiness_has_no_file_or_old_secret_access(self):
        class Provider:
            def get_key(self):
                return bytes(range(32))

        store = object.__new__(SQLiteSecretStore)
        store.codec = AESGCMSecretCodec(Provider())
        self.assertEqual(
            store.encryption_readiness(),
            {"ready": True, "state": "ready", "reason_code": "ready"},
        )
        store.codec = None
        self.assertEqual(store.encryption_readiness()["state"], "unavailable")


class UnifiedManagementHostTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = admin_fixture.AdminPagesTests.asyncSetUp
    asyncTearDown = admin_fixture.AdminPagesTests.asyncTearDown
    call = admin_fixture.AdminPagesTests.call

    async def recover(self):
        rows = (await self.call("read", {})).json()["data"]
        response = await self.call(
            "update",
            {
                "module_id": "game_link/core",
                "expected_revision": rows["game_link/core"]["revision"],
                "updates": [
                    {"field": "default_region", "mode": "replace", "value": "cn"}
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        rows = (await self.call("read", {})).json()["data"]
        response = await self.call(
            "recover",
            {
                "expected_revisions": {
                    owner: row["revision"] for owner, row in rows.items()
                },
                "complete_from_current": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    async def test_disable_unload_preserves_configuration_and_trusted_restore(self):
        await self.recover()
        core = self.runtime.core_runtime
        with patch.dict(
            os.environ, {"YGL_SECRET_KEY": base64.b64encode(bytes(range(32))).decode()}
        ):
            credential = (await self.call("credential-status", {})).json()["data"][
                "ff14/ff14"
            ]
            saved = await self.call(
                "credential-update",
                {
                    "module_id": "ff14/ff14",
                    "expected_revision": credential["revision"],
                    "updates": [
                        {
                            "field": "credential_fflogs_cn",
                            "mode": "replace",
                            "value": {
                                "client_id": "synthetic-id",
                                "client_secret": "synthetic-secret",
                            },
                        }
                    ],
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
        from ygl_test_subject.api.services import ConfigTarget

        target = ConfigTarget("astrbot_plugin_yomihime_game_link", "ff14/ff14")
        saved_metadata = (await core.config_repository.current(target)).secret_metadata
        before = (await self.call("read", {})).json()["data"]
        response = await self.call("modules", {})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["data"]
        response = await self.call(
            "module-enabled",
            {
                "module_id": "ff14/ff14",
                "enabled": False,
                "expected_registry_revision": row["registry_revision"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(core.registry.is_active("ff14/ff14"))
        self.assertEqual((await self.call("read", {})).json()["data"], before)
        response = await self.call(
            "module-unload",
            {
                "module_id": "ff14/ff14",
                "expected_registry_revision": core.registry.snapshot().revision,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["data"]["data_retained"])
        self.assertNotIn("ff14/ff14", core.registry.snapshot().modules)
        self.assertEqual(
            (await core.config_repository.current(target)).secret_metadata,
            saved_metadata,
        )
        self.assertEqual(
            (await self.call("read", {})).json()["data"],
            {"game_link/core": before["game_link/core"]},
        )
        self.assertEqual(
            (await self.call("credential-catalog", {})).json()["data"]["fields"], []
        )
        self.assertEqual((await self.call("credential-status", {})).json()["data"], {})
        denied = await self.call(
            "credential-update",
            {
                "module_id": "ff14/ff14",
                "expected_revision": before["ff14/ff14"]["revision"],
                "updates": [{"field": "credential_fflogs_cn", "mode": "clear"}],
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(
            any(
                f["module_id"] == "ff14/ff14"
                for f in (await self.call("catalog", {})).json()["data"]["fields"]
            )
        )
        response = await self.call(
            "module-enabled",
            {
                "module_id": "ff14/ff14",
                "enabled": True,
                "expected_registry_revision": core.registry.snapshot().revision,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(core.registry.is_active("ff14/ff14"))
        self.assertEqual(
            (await core.config_repository.current(target)).secret_metadata,
            saved_metadata,
        )
        self.assertEqual(
            (await self.call("credential-status", {})).json()["data"]["ff14/ff14"][
                "fields"
            ]["credential_fflogs_cn"],
            "configured",
        )
        response = await self.call(
            "module-enabled",
            {
                "module_id": "other/demo",
                "enabled": True,
                "expected_registry_revision": core.registry.snapshot().revision,
            },
        )
        self.assertEqual(response.status_code, 403)

    async def test_late_ordinary_and_credential_update_cannot_commit_after_unload(self):
        import asyncio

        from ygl_test_subject.api.administration import AdminOperation
        from ygl_test_subject.api.services import ConfigTarget

        await self.recover()
        for endpoint, updates in (
            (
                "update",
                [
                    {
                        "field": "ff14_calendar_default_days",
                        "mode": "replace",
                        "value": 12,
                    }
                ],
            ),
            ("credential-update", [{"field": "credential_fflogs_cn", "mode": "clear"}]),
        ):
            core = self.runtime.core_runtime
            ops = core.admin_operations
            target = ConfigTarget("astrbot_plugin_yomihime_game_link", "ff14/ff14")
            before = await core.config_repository.current(target)
            entered, release = asyncio.Event(), asyncio.Event()
            original = ops.authorization.authorize

            async def paused(operation, **kwargs):
                grant = await original(operation, **kwargs)
                if operation is AdminOperation.UPDATE_CONFIG:
                    entered.set()
                    await release.wait()
                return grant

            with patch.object(ops.authorization, "authorize", paused):
                pending = asyncio.create_task(
                    self.call(
                        endpoint,
                        {
                            "module_id": "ff14/ff14",
                            "expected_revision": before.revision,
                            "updates": updates,
                        },
                    )
                )
                await asyncio.wait_for(entered.wait(), 5)
                result = await self.call(
                    "module-unload",
                    {
                        "module_id": "ff14/ff14",
                        "expected_registry_revision": core.registry.snapshot().revision,
                    },
                )
                self.assertEqual(result.status_code, 200, result.text)
                release.set()
                result = await pending
                self.assertEqual(result.status_code, 403, result.text)
            after = await core.config_repository.current(target)
            self.assertEqual(after.values, before.values)
            self.assertEqual(after.secret_metadata, before.secret_metadata)
            result = await self.call(
                "module-enabled",
                {
                    "module_id": "ff14/ff14",
                    "enabled": True,
                    "expected_registry_revision": core.registry.snapshot().revision,
                },
            )
            self.assertEqual(result.status_code, 200, result.text)

    async def test_preflight_current_codec_only_and_no_credential_projection(self):
        with patch.dict(os.environ, {}, clear=True):
            response = await self.call("credential-readiness", {})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["state"], "unavailable")
        with patch.dict(
            os.environ, {"YGL_SECRET_KEY": base64.b64encode(bytes(range(32))).decode()}
        ):
            response = await self.call("credential-readiness", {})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["data"],
                {"ready": True, "state": "ready", "reason_code": "ready"},
            )
        self.assertEqual(
            (await self.call("credential-readiness", {"key": "synthetic"})).status_code,
            400,
        )


class NeutralPublicHostTests(unittest.IsolatedAsyncioTestCase):
    """Actual fixed Host handler, Runtime, Core and disk handlers; no game gate."""

    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path

        from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
        from ygl_test_subject.api.display import DisplayLimits
        from ygl_test_subject.services.core_runtime import CoreRuntime

        from tests.fixtures.settings_module import write_settings_module
        from tests.host.test_ff14_pages import _Context
        from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer
        from tests.services.test_unified_settings import NeutralModuleSettingsTests

        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        self.owners = tuple(
            write_settings_module(extensions, p, p)
            for p in ("first", "second", "denied")
        )
        self.runtime = AstrBotRuntime(
            _Context(),
            plugin_root=self.root,
            data_dir=self.root,
            config={"web_public_origin": "https://ui.test"},
        )
        self.runtime._generation = 1
        self.runtime._ready = True
        self.validator = HostPublicWebValidator(
            1,
            self.runtime._web_current,
            bindings={
                (owner, "items", "inspect"): (owner, "inspect") for owner in self.owners
            },
        )
        self.core = CoreRuntime(
            database=self.root / "runtime.sqlite3",
            extension_root=extensions,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda r: r,
            renderer=_Renderer(),
            display_limits=DisplayLimits(2, 4096),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="fixture",
            identity_namespace="fixture",
            pump_interval=3600,
            cleanup_timeout=1,
            managed_module_owners=set(self.owners),
            public_web_validator=self.validator,
            public_web_capabilities=frozenset(
                (owner, capability)
                for owner in self.owners[:2]
                for capability in ("inspect", "private.inspect", "write.change")
            ),
        )
        self.runtime._core = self.core
        self.runtime._web_validator = self.validator
        self.validator.attach(self.core)
        await self.core.start()
        from ygl_test_subject.api.administration import AdminOperation

        self.modules = self.core.admin_authorization.register_source(
            "modules",
            resources=self.core.admin_operations.module_resources(),
            operations={
                AdminOperation.LIST_MODULES,
                AdminOperation.SET_ENABLED,
                AdminOperation.UNLOAD_MODULE,
            },
        )
        self.proof = NeutralModuleSettingsTests.proof.__get__(self)
        context = self.proof(self.modules)
        for owner in self.owners:
            await self.core.admin_operations.set_enabled(
                None,
                owner,
                True,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=context,
            )
        self.modules.end(context)
        from ygl_test_subject.adapters.astrbot.ff14_pages import FF14Pages

        self.pages = FF14Pages(_Context(), self.runtime)
        self.pages.register()

    async def asyncTearDown(self):
        self.pages.close()
        await self.core.close(timeout=1)
        self.temp.cleanup()

    async def call(self, owner, parameters=None, *, page="items", capability="inspect"):
        import sys

        from tests.host.test_ff14_pages import _host_contracts, _request

        web, _ = _host_contracts()
        request = _request(
            web,
            body={
                "owner": owner,
                "page": page,
                "capability_id": capability,
                "parameters": parameters or {},
            },
            path="/api/plug/astrbot_plugin_yomihime_game_link/invoke",
        )
        handler = next(
            h for route, h in self.pages._registrations if route.endswith("/invoke")
        )
        with (
            patch.dict(sys.modules, {"astrbot.api.web": web}),
            web.bind_request_context(request),
        ):
            return await handler()

    async def test_two_owners_same_page_and_declaration_cannot_authorize(self):
        for owner in self.owners[:2]:
            result = await self.call(owner)
            self.assertEqual(result.status_code, 200, result.body)
            self.assertEqual(
                json.loads(result.body)["data"]["model_facts"], {"owner": owner}
            )
        result = await self.call(self.owners[2])
        self.assertNotEqual(
            json.loads(result.body).get("data", {}).get("status"), "success"
        )
        for page, cap in (
            ("other", "inspect"),
            ("items", "undeclared.write"),
            ("items", "private.read"),
        ):
            result = await self.call(self.owners[0], page=page, capability=cap)
            self.assertGreaterEqual(result.status_code, 400)
        for capability in ("private.inspect", "write.change"):
            rejected = await self.call(self.owners[0], capability=capability)
            self.assertNotEqual(
                json.loads(rejected.body).get("data", {}).get("status"), "success"
            )
        ticket = self.validator.begin(
            (self.owners[0], "items", "inspect"),
            "synthetic",
            int(__import__("time").time()) + 30,
        )
        proof = self.validator.mint(ticket)
        result = await self.core.invoke_public_web(
            self.owners[1], "inspect", {}, proof=proof
        )
        self.assertNotEqual(result.status.value, "success")
        self.validator.revoke(ticket)

    async def test_public_facts_512_513_and_document_32_33_boundaries(self):
        for parameters, allowed in (
            ({"nodes": 509}, True),
            ({"nodes": 510}, False),
            ({"blocks": 32}, True),
            ({"blocks": 33}, False),
            ({"privacy": "private"}, False),
        ):
            result = await self.call(self.owners[0], parameters)
            data = json.loads(result.body).get("data", {})
            self.assertEqual(
                data.get("status") == "success", allowed, (parameters, result.body)
            )
        from ygl_test_subject.adapters.astrbot.web_public import (
            WebPublicRejected,
            project_result,
        )
        from ygl_test_subject.api.display import DisplayDocument, TextBlock
        from ygl_test_subject.api.results import (
            CapabilityResult,
            FactDocument,
            ResultStatus,
        )

        nested = CapabilityResult(
            "neutral",
            ResultStatus.SUCCESS,
            DisplayDocument("N", "N", (TextBlock("N"),)),
            model_facts=FactDocument(
                {"rows": [{"a": 1, "b": 2, "c": 3} for _ in range(200)]}
            ),
        )
        with self.assertRaises(WebPublicRejected):
            project_result(nested)
        original = self.core.resource_visibility.contains_non_public_resource_reference

        async def private(value):
            return value == "private-fixture-resource"

        self.core.resource_visibility.contains_non_public_resource_reference = private
        try:
            result = await self.call(
                self.owners[0], {"reference": "private-fixture-resource"}
            )
            self.assertNotEqual(json.loads(result.body)["data"]["status"], "success")
        finally:
            self.core.resource_visibility.contains_non_public_resource_reference = (
                original
            )

    async def test_old_epoch_disable_and_unload_deny_public_execution(self):
        owner = self.owners[0]
        context = self.proof(self.modules)
        ticket = self.validator.begin(
            (owner, "items", "inspect"),
            "synthetic",
            int(__import__("time").time()) + 30,
        )
        proof = self.validator.mint(ticket)
        await self.core.admin_operations.set_enabled(
            None,
            owner,
            False,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        result = await self.core.invoke_public_web(owner, "inspect", {}, proof=proof)
        self.assertNotEqual(result.status.value, "success")
        self.validator.revoke(ticket)
        result = await self.call(owner)
        self.assertNotEqual(
            json.loads(result.body).get("data", {}).get("status"), "success"
        )
        await self.core.admin_operations.unload_module(
            None,
            owner,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        result = await self.call(owner)
        self.assertNotEqual(
            json.loads(result.body).get("data", {}).get("status"), "success"
        )
        self.modules.end(context)
