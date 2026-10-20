"""Offline production discovery/load/page-proof chain; no registry injection."""

import asyncio
import importlib.util
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from ygl_test_subject.adapters.astrbot.web_public import (
    HostPublicWebValidator,
    WebPublicRejected,
)
from ygl_test_subject.core.contracts.administration import AdminOperation
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.page_resources import capture_page_resources
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.trusted_assembly import (
    CapturedAssemblyInventory,
    assemble_reviewed,
)

from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer
from yomihime_game_link_sdk.display import DisplayLimits

SOURCE = Path(__file__).resolve().parents[3] / "examples/modules/minimal"
OWNER = "minimal/demo"


class MinimalFormalChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-minimal-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "extensions/minimal"
        shutil.copytree(
            SOURCE, self.package, ignore=shutil.ignore_patterns("__pycache__")
        )
        packages = discover_packages(self.root / "extensions")
        self.assertEqual(len(packages), 1)
        self.assertTrue(packages[0].valid, packages[0].diagnostic)
        self.discovered = packages[0]
        spec = importlib.util.spec_from_file_location(
            "minimal_reviewed_support", SOURCE / "assembly.py"
        )
        support_module = importlib.util.module_from_spec(spec)
        # Dataclasses resolve their own module during class creation.
        import sys

        sys.modules[spec.name] = support_module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(support_module)
        inventory = CapturedAssemblyInventory(
            "minimal",
            (self.package / "yomihime.manifest.json").read_bytes(),
            {"assembly.json": (self.package / "assembly.json").read_bytes()},
        )
        with self.assertRaises(ValueError):
            assemble_reviewed(
                inventory, principal_id="fixture", support=support_module.SUPPORT
            )
        assembly = assemble_reviewed(
            inventory,
            principal_id="fixture",
            support=support_module.SUPPORT,
            selected=True,
        )
        self.host_generation = 1
        self.host_active = True
        self.host_core = None
        self.validator = HostPublicWebValidator(
            self.host_generation,
            self.host_current,
            bindings={(OWNER, "home", "status"): (OWNER, "status")},
        )
        self.core = CoreRuntime(
            database=self.root / "runtime.sqlite3",
            extension_root=self.root / "extensions",
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=validate_contract(DisplayLimits(2, 4096)),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="fixture",
            identity_namespace="fixture",
            pump_interval=3600,
            cleanup_timeout=1,
            managed_module_owners={OWNER},
            public_web_validator=self.validator,
            public_web_capabilities=assembly.public_capabilities,
        )
        self.host_core = self.core
        self.validator.attach(self.core)
        await self.core.start()
        self.addAsyncCleanup(self.core.close, timeout=1)
        self.source = self.core.admin_authorization.register_source(
            "minimal-lifecycle",
            resources=self.core.admin_operations.module_resources(),
            operations={
                AdminOperation.LIST_MODULES,
                AdminOperation.SET_ENABLED,
                AdminOperation.UNLOAD_MODULE,
            },
        )
        await self.enable(True)

    def host_current(self, core, generation):
        """Offline trusted Host state: exact Core, live session and generation."""
        return (
            self.host_active
            and core is self.host_core
            and generation == self.host_generation
            and not core.closed
        )

    async def enable(self, enabled):
        request = object()
        proof = self.source.issue(
            subject="synthetic",
            request=request,
            expiry=time.time() + 30,
            operations=self.source.operations,
            resources=self.source.resources,
            live=lambda actual: actual is request,
        )
        try:
            return await self.core.admin_operations.set_enabled(
                None,
                OWNER,
                enabled,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=proof,
            )
        finally:
            self.source.end(proof)

    def proof(self):
        ticket = self.validator.begin(
            (OWNER, "home", "status"), "synthetic", int(time.time()) + 30
        )
        return ticket, self.validator.mint(ticket)

    async def test_discovery_capture_loader_authorization_and_restore(self):
        self.assertEqual(tuple(self.core.registry.snapshot().modules), (OWNER,))
        resources = capture_page_resources(
            self.discovered._provenance, self.discovered.manifest
        )
        self.assertTrue(resources)
        ticket, proof = self.proof()
        result = await self.core.invoke_public_web(OWNER, "status", {}, proof=proof)
        self.assertEqual(result.status.value, "success")
        self.assertEqual(
            dict(result.model_facts.facts), {"sample": "minimal", "ready": True}
        )
        denied = await self.core.invoke_public_web(
            "other/demo", "status", {}, proof=proof
        )
        self.assertNotEqual(denied.status.value, "success")
        self.validator.revoke(ticket)
        old_ticket, old_proof = self.proof()
        await self.enable(False)
        stale = await self.core.invoke_public_web(OWNER, "status", {}, proof=old_proof)
        self.assertNotEqual(stale.status.value, "success")
        await self.enable(True)
        stale = await self.core.invoke_public_web(OWNER, "status", {}, proof=old_proof)
        self.assertNotEqual(stale.status.value, "success")
        self.validator.revoke(old_ticket)
        fresh_ticket, fresh = self.proof()
        restored = await self.core.invoke_public_web(OWNER, "status", {}, proof=fresh)
        self.assertEqual(restored.status.value, "success")
        self.validator.revoke(fresh_ticket)

    async def test_host_session_generation_core_and_close_fence(self):
        self.assertTrue(self.host_current(self.core, 1))
        self.assertFalse(self.host_current(object(), 1))
        self.assertFalse(self.host_current(self.core, 2))
        for change, restore in (
            (
                lambda: setattr(self, "host_active", False),
                lambda: setattr(self, "host_active", True),
            ),
            (
                lambda: setattr(self, "host_generation", 2),
                lambda: setattr(self, "host_generation", 1),
            ),
            (
                lambda: setattr(self, "host_core", object()),
                lambda: setattr(self, "host_core", self.core),
            ),
        ):
            ticket, proof = self.proof()
            change()
            with self.assertRaises(WebPublicRejected):
                self.validator.remaining(ticket)
            denied = await self.core.invoke_public_web(OWNER, "status", {}, proof=proof)
            self.assertNotEqual(denied.status.value, "success")
            self.validator.revoke(ticket)
            restore()
            fresh_ticket, fresh = self.proof()
            result = await self.core.invoke_public_web(OWNER, "status", {}, proof=fresh)
            self.assertEqual(result.status.value, "success")
            self.validator.revoke(fresh_ticket)
        ticket, proof = self.proof()
        binding = self.validator.consume(
            proof, module_id=OWNER, capability_id="status", generation=1
        )
        self.assertTrue(self.validator.is_current(proof, binding))
        await self.core.close(timeout=1)
        self.assertFalse(self.host_current(self.core, 1))
        self.assertFalse(self.validator.is_current(proof, binding))
        with self.assertRaises(WebPublicRejected):
            self.validator.remaining(ticket)
        self.validator.revoke(ticket)

    async def test_host_close_while_formal_invocation_is_pending(self):
        ticket, proof = self.proof()
        request = asyncio.create_task(
            self.core.invoke_public_web(OWNER, "status", {}, proof=proof)
        )
        await asyncio.sleep(0)
        self.assertFalse(request.done(), "request must still be pending at closure")
        self.host_active = False
        await self.core.close(timeout=1)
        outcome = (await asyncio.gather(request, return_exceptions=True))[0]
        self.assertTrue(
            isinstance(outcome, BaseException) or outcome.status.value != "success"
        )
        self.assertFalse(self.host_current(self.core, 1))
        with self.assertRaises(WebPublicRejected):
            self.validator.remaining(ticket)
        self.validator.revoke(ticket)


if __name__ == "__main__":
    unittest.main()
