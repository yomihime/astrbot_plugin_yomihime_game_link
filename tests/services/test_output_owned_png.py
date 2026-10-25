"""Real producer/resource/renderer/PreparedCommand boundary for one owner PNG."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import MessageReceipt, MessageStatus
from ygl_test_subject.presentation.command_card import CommandCardRenderer
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
)
from ygl_test_subject.services.core_runtime import CoreRuntime, HostIngress

from tests.fixtures.b03_runtime import _TestCodec
from tests.integration.test_managed_resources import png
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
)
from yomihime_game_link_sdk.display import (
    DisplayDocument,
    DisplayLimits,
    ImageBlock,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from yomihime_game_link_sdk.storage import CacheVisibility
from yomihime_game_link_sdk.subscriptions import ConversationKind
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION

OWNER = "ownedpng/sample"
CONTENT = png(23)


class _PreparedMessages:
    """Synthetic final Host dispatch; Core preparation and guards stay real."""

    def __init__(self):
        self.calls = []
        self.on_prepare = None
        self.prepare_count = 0

    async def send(self, target, payload):
        self.calls.append((target, payload))
        return MessageReceipt(MessageStatus.ACCEPTED, "fixture-message")

    async def send_command(self, *args, **kwargs):
        raise AssertionError("private owner PNG must use the prepared dispatch")

    def prepare_command(self, ingress, target, payload, *, image_current):
        self.prepare_count += 1
        if self.on_prepare is not None:
            self.on_prepare(ingress)

        async def dispatch():
            image_current()
            self.calls.append((target, payload))
            return MessageReceipt(MessageStatus.ACCEPTED, "fixture-prepared")

        return SimpleNamespace(check_current=image_current, dispatch=dispatch)


class _Producer:
    def __init__(self):
        self.services = None
        self.asset_id = None
        self.expire_before_output = False

    async def invoke(self, invocation, parameters):
        scope = await self.services.scopes.bind(invocation)
        if self.asset_id is None:
            reference = await scope.resources.store(
                "image/png",
                CONTENT,
                visibility=CacheVisibility.USER,
                ttl_seconds=1 if self.expire_before_output else 60,
            )
            self.asset_id = reference.asset_id
        if self.expire_before_output:
            # Expire the real metadata naturally, with no mocked clock/read.
            await asyncio.sleep(1.1)
        return CapabilityResult(
            "one-owned-png",
            ResultStatus.SUCCESS,
            document=DisplayDocument(
                "Owned PNG",
                "Producer-consumer regression",
                (
                    TextBlock("One current owner image"),
                    ImageBlock(
                        self.asset_id, "Owner image", visibility=Privacy.PRIVATE
                    ),
                ),
                privacy=Privacy.PRIVATE,
            ),
            privacy=Privacy.PRIVATE,
        )


class _Module:
    def __init__(self, producer):
        self.producer = producer

    def handlers(self):
        return ModuleHandlers({"read": self.producer}, {}, {})

    async def start(self):
        pass

    async def stop(self):
        pass

    async def check_health(self):
        return HealthReport({"read": CapabilityHealth(HealthStatus.AVAILABLE)})


class OwnedPngOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        extensions = directory / "extensions"
        extensions.mkdir()
        self.evidence = object()
        self.messages = _PreparedMessages()
        self.producer = _Producer()
        self.runtime = CoreRuntime(
            database=directory / "core.sqlite3",
            extension_root=extensions,
            file_root=directory / "files",
            secret_root=directory / "secrets",
            secret_codec=_TestCodec(),
            http_transport=lambda request: request,
            renderer=GenericDisplayRenderer(
                RenderingBounds(6000, 200, 100, 100, 4194304, 4096, 64, 256, 64)
            ),
            command_card_renderer=CommandCardRenderer(),
            display_limits=DisplayLimits(8, 24000),
            message_port=self.messages,
            admin_context_validator=lambda *_: True,
            host_ingress_validator=lambda origin, ingress: ingress.evidence
            is self.evidence,
            config_principal_id="owned-png-config",
            identity_namespace="owned-png-users",
            pump_interval=3600,
            cleanup_timeout=2,
        )
        self.addAsyncCleanup(self._close_runtime)
        await self.runtime.start()
        manifest = validate_contract(
            ModuleManifest(
                "sample",
                "ownedpng",
                ModuleCategory.GAME,
                "module:Factory",
                "1.0.0",
                (
                    CapabilityDescriptor(
                        "read",
                        {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        InvocationPolicy.COMMAND_ONLY,
                        CapabilityEffect.READ_ONLY,
                        privacy_floor=PrivacyFloor.OWNER,
                    ),
                ),
                commands=(CommandDescriptor("read", "read", {}, "Read owner PNG"),),
            )
        )
        instance = _Module(self.producer)
        package = PackageManifest(
            "ownedpng", "1.0.0", MODULE_ABI_VERSION, (manifest,), "tests", "MIT", "test"
        )
        runtime = self.runtime
        runtime.registry.register_package(package, {"sample": instance.handlers()})
        handlers = runtime.lifecycle.adopt_candidate(
            "ownedpng", manifest, "fixture-install", instance
        )
        runtime.lifecycle.install_dormant(
            "ownedpng", OWNER, "fixture-install", instance, handlers
        )
        identity, _ = await runtime.lifecycle.start_candidate(OWNER, "fixture-start")
        await runtime.health_resolver.prepare(OWNER, manifest)
        runtime.lifecycle.publish_committed_intent(
            OWNER, "fixture-start", identity, True, runtime.registry.snapshot().revision
        )
        self.producer.services = runtime.module_services.for_module(OWNER)

    async def _close_runtime(self):
        self.assertTrue(await self.runtime.close(timeout=5))

    async def command(self, actor="alice"):
        return await self.runtime.invoke_command(
            OWNER,
            "read",
            {},
            ingress=HostIngress(
                "fixture-im",
                actor,
                "direct-room",
                "fixture-route",
                ConversationKind.DIRECT,
                self.evidence,
            ),
        )

    async def test_private_single_image_reaches_prepared_sender_as_exact_png_and_true_text(
        self,
    ):
        outcome = await self.command()
        self.assertEqual(outcome.output.status.value, "sent")
        self.assertIsNotNone(outcome.result)
        self.assertEqual(self.messages.prepare_count, 1)
        self.assertEqual(len(self.messages.calls), 1)
        payload = self.messages.calls[0][1]
        self.assertEqual(payload.png_pages, (CONTENT,))
        self.assertEqual(payload.resource_ids, ())
        self.assertIsNone(payload.png_bytes)
        self.assertIn("One current owner image", payload.text)
        self.assertNotIn("图片不可用", payload.text)

    async def test_original_issuer_revocation_during_prepare_is_controlled_and_never_sends(
        self,
    ):
        def revoke(ingress):
            invocation, _ = next(
                pair
                for pair in self.runtime._command_ingresses.values()
                if pair[1] is ingress
            )
            self.runtime.issuer.release(invocation)

        self.messages.on_prepare = revoke
        outcome = await self.command()
        self.assertEqual(outcome.output.status.value, "failed")
        self.assertIsNone(outcome.result)
        self.assertIsNone(outcome.output.result)
        self.assertEqual(self.messages.prepare_count, 1)
        self.assertEqual(self.messages.calls, [])

    async def test_other_command_owner_cannot_render_original_owners_png(self):
        self.assertEqual((await self.command()).output.status.value, "sent")
        self.messages.calls.clear()
        outcome = await self.command("bob")
        self.assertEqual(outcome.output.status.value, "failed")
        self.assertIsNone(outcome.result)
        self.assertEqual(self.messages.calls, [])
        self.assertEqual((await self.command()).output.status.value, "sent")
        self.assertEqual(self.messages.calls[0][1].png_pages, (CONTENT,))

    async def test_expired_resource_metadata_never_reaches_prepared_sender(self):
        self.producer.expire_before_output = True
        outcome = await self.command()
        self.assertEqual(outcome.output.status.value, "failed")
        self.assertIsNone(outcome.result)
        self.assertEqual(self.messages.prepare_count, 0)
        self.assertEqual(self.messages.calls, [])
