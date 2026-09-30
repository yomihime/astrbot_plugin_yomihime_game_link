"""AstrBot host checks against the real CoreRuntime and bundled module."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from ygl_test_subject.adapters.astrbot.message_port import AstrBotMessagePort
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.api.administration import AdminAuthorizationGrant, AdminOperation
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
)
from ygl_test_subject.api.services import HealthStatus, HttpResponse, ModuleHandlers
from ygl_test_subject.api.subscriptions import ConversationKind, ConversationRef
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.ports import (
    MessageStatus,
    MessageTarget,
    RenderedMessage,
)
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_runtime import (
    RuntimeJournalPhase,
    SQLiteModuleRuntimeRepository,
)
from ygl_test_subject.services.core_runtime import (
    CoreRuntimeCleanupPending,
    HostIngress,
)

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _Plain:
    text: str


@dataclass(frozen=True, slots=True)
class _MessageChain:
    chain: list[object]


class _Context:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.error = error

    async def send_message(self, session: str, message_chain: object) -> bool:
        self.calls.append((session, message_chain))
        if self.error is not None:
            raise self.error
        return True


class _ProxyContext(_Context):
    def get_config(self) -> dict[str, object]:
        return {"http_proxy": "http://127.0.0.1:7890"}


class _ClosePendingCore:
    def __init__(self) -> None:
        self.registry = Registry()
        self.close_calls = 0

    async def start(self) -> None:
        return None

    async def close(self) -> bool:
        self.close_calls += 1
        return False


class _IdleTransport:
    def __init__(self) -> None:
        self.close_calls = 0
        self.closed = False

    async def request(self, _request):
        raise AssertionError("the lifecycle test must not perform HTTP")

    async def request_credential_exchange(self, _request):
        raise AssertionError("the item/status test must not exchange credentials")

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class _ItemReplyTransport(_IdleTransport):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[object] = []

    async def request(self, request) -> HttpResponse:
        self.requests.append(request)
        if request.source_id == "xivapi_items":
            body = {
                "row_id": 100,
                "fields": {
                    "Name": "Copper Ore",
                    "Description": "A test item.",
                    "LevelEquip": 1,
                    "LevelItem": {"value": 1},
                },
            }
        elif request.source_id == "garland_items":
            body = {"item": {"id": 100}, "partials": []}
        else:
            raise AssertionError("unexpected source id")
        return HttpResponse(200, {}, json.dumps(body).encode("utf-8"))


class _Event:
    def __init__(self, message_type: str = "GroupMessage") -> None:
        self.message = "/ygl ff14 status"
        self.platform = "test-platform-1"
        self.sender = "user-17"
        self.conversation = "group-42"
        self.message_type = message_type
        self.role = "member"

    def get_message_str(self) -> str:
        return self.message

    def get_platform_id(self) -> str:
        return self.platform

    def get_sender_id(self) -> str:
        return self.sender

    def get_session_id(self) -> str:
        return self.conversation

    def get_message_type(self) -> str:
        return self.message_type


class _OwnerHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, _context, _parameters):
        self.calls += 1
        raise AssertionError("a group OWNER command must stop before its handler")


class AstrBotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ygl-astrbot-runtime-", dir=ROOT
        )
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / "plugin-data"
        self.data_dir.mkdir()
        self.plugin_root = self.root / "plugin"
        (self.plugin_root / "modules").mkdir(parents=True)
        shutil.copytree(
            ROOT / "modules" / "ff14",
            self.plugin_root / "modules" / "ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _rewrite_source(
        self, source_id: str, *, host: str | None = None, remove: bool = False
    ) -> None:
        manifest_path = self.plugin_root / "modules" / "ff14" / "yomihime.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        module = manifest["modules"][0]
        sources = module.get("sources", [])
        if remove:
            module["sources"] = [
                source for source in sources if source["source_id"] != source_id
            ]
        else:
            for source in sources:
                if source["source_id"] == source_id:
                    source["host"] = host
                    break
            else:
                raise AssertionError(f"missing source in fixture manifest: {source_id}")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _rewrite_config_field(self, name: str, **updates: object) -> None:
        manifest_path = self.plugin_root / "modules" / "ff14" / "yomihime.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fields = manifest["modules"][0].get("config_fields", [])
        for field in fields:
            if field["name"] == name:
                field.update(updates)
                break
        else:
            raise AssertionError(f"missing config field in fixture manifest: {name}")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _runtime(
        self,
        context: _Context,
        *,
        http_transport_factory=None,
    ) -> AstrBotRuntime:
        return AstrBotRuntime(
            context,
            plugin_root=self.plugin_root,
            data_dir=self.data_dir,
            plain_factory=_Plain,
            chain_factory=lambda components: _MessageChain(list(components)),
            http_transport_factory=http_transport_factory,
        )

    async def test_initialize_is_single_and_real_core_command_reaches_message_chain(
        self,
    ):
        context = _Context()
        runtime = self._runtime(context)

        await asyncio.gather(
            runtime.initialize(), runtime.initialize(), runtime.initialize()
        )
        core = runtime.core_runtime
        self.assertIsNotNone(core)
        transport = runtime._http_transport
        self.assertIsInstance(transport, AioHttpTransport)
        self.assertTrue(runtime.ready)
        self.assertIs(core, runtime.core_runtime)
        module = core.registry.snapshot().module("ff14/ff14")
        self.assertTrue(module.enabled)
        expected_packages = discover_packages(self.plugin_root / "modules")
        self.assertEqual(len(expected_packages), 1)
        self.assertEqual(module.manifest, expected_packages[0].manifest.modules[0])
        self.assertNotEqual(core.extension_root, self.data_dir / "extensions")

        self.assertIsNone(await runtime.handle_event(_Event()))
        self.assertEqual(len(context.calls), 1)
        session, chain = context.calls[0]
        self.assertEqual(
            session,
            "test-platform-1:GroupMessage:group-42",
        )
        self.assertIsInstance(chain, _MessageChain)
        self.assertEqual(len(chain.chain), 1)
        self.assertIsInstance(chain.chain[0], _Plain)
        self.assertIn("FF14", chain.chain[0].text)
        self.assertIn("FFLogs", chain.chain[0].text)

        await runtime.terminate()
        self.assertTrue(core.closed)
        self.assertTrue(transport.closed)
        self.assertFalse(runtime.ready)
        await runtime.terminate()

    async def test_content_addressed_bundle_upgrade_preserves_enabled_intent(self):
        runtime = self._runtime(_Context())
        await runtime.initialize()
        first_core = runtime.core_runtime
        self.assertIsNotNone(first_core)
        self.assertTrue(first_core.registry.snapshot().module("ff14/ff14").enabled)
        first_extension_root = first_core.extension_root
        await runtime.terminate()
        self.assertTrue(first_core.closed)

        readme = self.plugin_root / "modules" / "ff14" / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nUpdated reviewed bundle.\n")

        await runtime.initialize()
        changed_core = runtime.core_runtime
        self.assertIsNotNone(changed_core)
        self.assertNotEqual(changed_core.extension_root, first_extension_root)
        self.assertTrue(first_extension_root.is_dir())
        self.assertTrue(changed_core.registry.snapshot().module("ff14/ff14").enabled)
        persisted = await changed_core.runtime_repository.current_intent("ff14", "ff14")
        self.assertIsNotNone(persisted)
        self.assertTrue(persisted.desired_enabled)
        self.assertIsNone(runtime._isolated_extension_dir)
        await runtime.terminate()

    async def test_content_addressed_bundle_upgrade_preserves_disabled_intent(self):
        runtime = self._runtime(_Context())
        await runtime.initialize()
        first_core = runtime.core_runtime
        self.assertIsNotNone(first_core)
        first_extension_root = first_core.extension_root
        self.assertTrue(first_core.registry.snapshot().module("ff14/ff14").enabled)
        await runtime.terminate()

        database = SQLiteDatabase(self.data_dir / "runtime.sqlite3")
        runtime_repository = SQLiteModuleRuntimeRepository(database)
        credentials = SQLiteAdminCredentialRepository(database)
        await credentials.bootstrap(bytes(range(32)))
        grant = AdminAuthorizationGrant(AdminOperation.SET_ENABLED, 1)
        current = await runtime_repository.current_intent("ff14", "ff14")
        self.assertIsNotNone(current)
        prepared = await runtime_repository.prepare(
            "test-disable-before-upgrade",
            "ff14",
            "ff14",
            False,
            expected_intent_revision=current.intent_revision,
            expected_registry_revision=first_core.registry.snapshot().revision,
            grant=grant,
        )
        disabled = await runtime_repository.commit_intent(prepared.operation_id, grant)
        self.assertFalse(disabled.desired_enabled)
        await runtime_repository.mark_phase(
            prepared.operation_id, RuntimeJournalPhase.APPLIED
        )
        await database.executor.close()

        readme = self.plugin_root / "modules" / "ff14" / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nDisabled intent upgrade.\n")
        await runtime.initialize()

        changed_core = runtime.core_runtime
        self.assertIsNotNone(changed_core)
        self.assertNotEqual(changed_core.extension_root, first_extension_root)
        self.assertTrue(first_extension_root.is_dir())
        self.assertNotIn("ff14/ff14", changed_core.registry.snapshot().modules)
        persisted = await changed_core.runtime_repository.current_intent("ff14", "ff14")
        self.assertIsNotNone(persisted)
        self.assertFalse(persisted.desired_enabled)
        self.assertEqual(persisted.intent_revision, 2)
        await runtime.terminate()

    async def test_modified_existing_bundle_cannot_restore_enabled_intent(self):
        runtime = self._runtime(_Context())
        await runtime.initialize()
        first_core = runtime.core_runtime
        self.assertIsNotNone(first_core)
        self.assertTrue(first_core.registry.snapshot().module("ff14/ff14").enabled)
        package_dir = first_core.extension_root / "ff14"
        await runtime.terminate()

        manifest_path = package_dir / "yomihime.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["modules"][0]["commands"][0]["help_text"] += " changed"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed_manifest = manifest_path.read_bytes()

        await runtime.initialize()

        changed_core = runtime.core_runtime
        self.assertIsNotNone(changed_core)
        self.assertFalse(runtime._trusted_bundle)
        self.assertNotEqual(changed_core.extension_root, first_core.extension_root)
        self.assertEqual(changed_core.registry.snapshot().modules, {})
        persisted = await changed_core.runtime_repository.current_intent("ff14", "ff14")
        self.assertIsNotNone(persisted)
        self.assertTrue(persisted.desired_enabled)
        self.assertEqual(manifest_path.read_bytes(), changed_manifest)
        await runtime.terminate()

    async def test_other_message_and_changed_event_facts_are_rejected(self):
        context = _Context()
        runtime = self._runtime(context)
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        self.assertIn(
            "无法确认消息来源", await runtime.handle_event(_Event("OtherMessage"))
        )
        event = _Event()
        ingress = runtime._make_ingress(event)
        self.assertIsInstance(ingress, HostIngress)
        self.assertIsNone(
            await core.repositories.identities.find_principal(
                PLUGIN_NAME, "test-platform-1:user-17"
            )
        )
        event.conversation = "another-group"
        with self.assertRaises(PermissionError):
            await core.invoke_command("ff14/ff14", "status", {}, ingress=ingress)
        self.assertIsNone(
            await core.repositories.identities.find_principal(
                PLUGIN_NAME, "test-platform-1:user-17"
            )
        )
        self.assertEqual(context.calls, [])
        await runtime.terminate()

    async def test_trusted_commands_provision_one_adapter_scoped_principal(self):
        runtime = self._runtime(_Context())
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        events = []
        for adapter in ("adapter-a", "adapter-b"):
            for _ in range(4):
                event = _Event()
                event.platform = adapter
                event.sender = "same-user"
                event.conversation = "same-chat"
                events.append(event)
        await asyncio.gather(*(runtime.handle_event(event) for event in events))

        first = await core.repositories.identities.find_principal(
            PLUGIN_NAME, "adapter-a:same-user"
        )
        second = await core.repositories.identities.find_principal(
            PLUGIN_NAME, "adapter-b:same-user"
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.principal_id, second.principal_id)
        self.assertEqual(
            (first.identity_namespace, first.external_user_id),
            (PLUGIN_NAME, "adapter-a:same-user"),
        )
        self.assertEqual(
            (second.identity_namespace, second.external_user_id),
            (PLUGIN_NAME, "adapter-b:same-user"),
        )
        row_count = await core.database.executor.run_read(
            lambda unit: unit.connection.execute(
                "SELECT COUNT(*) FROM principals WHERE identity_namespace = ?",
                (PLUGIN_NAME,),
            ).fetchone()[0]
        )
        self.assertEqual(row_count, 2)
        await runtime.terminate()

    async def test_group_owner_command_returns_fixed_private_chat_hint(self):
        context = _Context()
        runtime = self._runtime(context)
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        handler = _OwnerHandler()
        capability = CapabilityDescriptor(
            "owner.manage",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
            privacy_floor=PrivacyFloor.OWNER,
        )
        module = ModuleManifest(
            "owner",
            "owner",
            ModuleCategory.GAME,
            "tests.host.test_astrbot_runtime:_OwnerHandler",
            "1.0.0",
            (capability,),
            commands=(CommandDescriptor("manage", "owner.manage", {}, "owner.manage"),),
        )
        package = PackageManifest(
            "hosttest",
            "1.0.0",
            CONTRACT_VERSION,
            (module,),
            "Host runtime test",
            "MIT",
            "offline test package",
        )
        core.registry.register_package(
            package,
            {
                "owner": ModuleHandlers(
                    capabilities={"owner.manage": handler},
                    collectors={},
                    evaluators={},
                )
            },
        )
        event = _Event()
        event.message = "/ygl owner manage"

        response = await runtime.handle_event(event)

        self.assertEqual(response, "该本人管理命令仅支持私聊，请前往私聊继续。")
        self.assertEqual(handler.calls, 0)
        self.assertEqual(context.calls, [])
        self.assertNotIn("user-17", response)
        self.assertNotIn("revision", response.lower())
        await runtime.terminate()

    async def test_send_value_error_after_call_begins_is_unknown(self):
        context = _Context(error=ValueError("platform send failed"))
        port = AstrBotMessagePort(
            context,
            plain_factory=_Plain,
            chain_factory=lambda components: _MessageChain(list(components)),
        )
        conversation = ConversationRef(
            "test-platform-1",
            ConversationKind.DIRECT,
            "user-17",
            "test-platform-1",
        )
        receipt = await port.send(
            MessageTarget("user-17", conversation=conversation),
            RenderedMessage("status"),
        )
        self.assertIs(receipt.status, MessageStatus.UNKNOWN)
        self.assertEqual(len(context.calls), 1)
        self.assertIsInstance(context.calls[0][1], _MessageChain)

    async def test_cleanup_pending_core_owner_is_not_replaced(self):
        created: list[_ClosePendingCore] = []

        def core_factory(**_kwargs) -> _ClosePendingCore:
            core = _ClosePendingCore()
            created.append(core)
            return core

        runtime = AstrBotRuntime(
            _Context(),
            plugin_root=self.plugin_root,
            data_dir=self.data_dir,
            core_factory=core_factory,
        )
        await runtime.initialize()
        owner = runtime.core_runtime
        self.assertIs(created[0], owner)

        with self.assertRaises(CoreRuntimeCleanupPending):
            await runtime.terminate()
        with self.assertRaises(CoreRuntimeCleanupPending):
            await runtime.initialize()

        self.assertIs(runtime.core_runtime, owner)
        self.assertEqual(len(created), 1)

    async def test_owned_http_transport_closes_once_after_core_shutdown(self):
        transport = _IdleTransport()
        runtime = self._runtime(_Context(), http_transport_factory=lambda: transport)

        await runtime.initialize()
        self.assertIs(runtime._http_transport, transport)
        self.assertFalse(transport.closed)

        await runtime.terminate()
        self.assertTrue(transport.closed)
        self.assertEqual(transport.close_calls, 1)
        self.assertIsNone(runtime._http_transport)

        await runtime.terminate()
        self.assertEqual(transport.close_calls, 1)

    async def test_proxy_source_is_only_the_trusted_host_config(self):
        runtime = self._runtime(_ProxyContext())
        self.assertEqual(runtime._configured_http_proxy(), "http://127.0.0.1:7890")

        without_config = self._runtime(_Context())
        self.assertIsNone(without_config._configured_http_proxy())

    async def test_item_command_uses_core_sources_and_public_output(self):
        context = _Context()
        transport = _ItemReplyTransport()
        runtime = self._runtime(context, http_transport_factory=lambda: transport)
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        item_health, _ = core.health_resolver.current("ff14/ff14", "item.lookup")
        self.assertIs(item_health.status, HealthStatus.AVAILABLE)

        event = _Event()
        event.message = "/ygl ff14 item 100"
        self.assertIsNone(await runtime.handle_event(event))

        self.assertEqual(
            [request.source_id for request in transport.requests],
            ["xivapi_items", "garland_items"],
        )
        self.assertEqual(
            [request.host for request in transport.requests],
            ["xivapi-v2.xivcdn.com", "garlandtools.cn"],
        )
        self.assertEqual(len(context.calls), 1)
        self.assertIn("Copper Ore", context.calls[0][1].chain[0].text)
        await runtime.terminate()
        self.assertTrue(transport.closed)

    async def test_unavailable_required_source_only_degrades_item_capability(self):
        transport = _IdleTransport()
        transport.closed = True
        runtime = self._runtime(_Context(), http_transport_factory=lambda: transport)
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        status_health, _ = core.health_resolver.current("ff14/ff14", "status")
        item_health, _ = core.health_resolver.current("ff14/ff14", "item.lookup")
        self.assertIs(status_health.status, HealthStatus.AVAILABLE)
        self.assertIs(item_health.status, HealthStatus.UNAVAILABLE)
        await runtime.terminate()

    async def test_missing_required_source_is_capability_level_unknown(self):
        self._rewrite_source("xivapi_items", remove=True)
        runtime = self._runtime(_Context())
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        status_health, _ = core.health_resolver.current("ff14/ff14", "status")
        item_health, _ = core.health_resolver.current("ff14/ff14", "item.lookup")
        self.assertIs(status_health.status, HealthStatus.AVAILABLE)
        self.assertIs(item_health.status, HealthStatus.UNKNOWN)
        await runtime.terminate()

    async def test_wrong_optional_source_host_does_not_block_item_lookup(self):
        self._rewrite_source("garland_items", host="unapproved.example")
        runtime = self._runtime(_Context())
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        status_health, _ = core.health_resolver.current("ff14/ff14", "status")
        item_health, _ = core.health_resolver.current("ff14/ff14", "item.lookup")
        self.assertIs(status_health.status, HealthStatus.AVAILABLE)
        self.assertIs(item_health.status, HealthStatus.AVAILABLE)
        await runtime.terminate()

    async def test_wrong_required_source_host_blocks_only_item_lookup(self):
        self._rewrite_source("xivapi_items", host="unapproved.example")
        runtime = self._runtime(_Context())
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertIsNotNone(core)

        status_health, _ = core.health_resolver.current("ff14/ff14", "status")
        item_health, _ = core.health_resolver.current("ff14/ff14", "item.lookup")
        self.assertIs(status_health.status, HealthStatus.AVAILABLE)
        self.assertIs(item_health.status, HealthStatus.UNAVAILABLE)
        await runtime.terminate()

    async def test_trusted_manifest_binds_exact_independent_fflogs_policies(self):
        runtime = self._runtime(_Context())

        await runtime.initialize()

        policies = runtime._source_credential_policies
        self.assertEqual(
            {(item.source_id, item.credential_ref) for item in policies},
            {
                ("fflogs_public_cn", "credential_fflogs_cn"),
                ("fflogs_public_global", "credential_fflogs_global"),
            },
        )
        by_source = {item.source_id: item for item in policies}
        self.assertEqual(by_source["fflogs_public_cn"].resource_host, "cn.fflogs.com")
        self.assertEqual(by_source["fflogs_public_cn"].token_host, "cn.fflogs.com")
        self.assertEqual(
            by_source["fflogs_public_global"].resource_host, "www.fflogs.com"
        )
        self.assertEqual(by_source["fflogs_public_global"].token_host, "www.fflogs.com")
        self.assertTrue(
            all(item.allowed_resource_paths == ("/api/v2/client",) for item in policies)
        )
        self.assertTrue(all(item.token_path == "/oauth/token" for item in policies))
        core = runtime.core_runtime
        self.assertIsNotNone(core)
        self.assertEqual(core.module_services._source_credential_policies, policies)
        await runtime.terminate()

    async def test_partial_fflogs_sensitive_field_declaration_fails_closed(self):
        self._rewrite_config_field("credential_fflogs_cn", required=True)
        runtime = self._runtime(_Context())

        with self.assertRaisesRegex(RuntimeError, "credential declaration"):
            await runtime.initialize()

        self.assertIsNone(runtime.core_runtime)
        self.assertFalse(runtime.ready)
        await runtime.terminate()

    async def test_missing_fflogs_secret_never_falls_back_to_anonymous_http(self):
        context = _Context()
        transport = _ItemReplyTransport()
        runtime = self._runtime(context, http_transport_factory=lambda: transport)
        await runtime.initialize()

        event = _Event()
        event.message = "/ygl ff14 logs global Aether Rada"
        self.assertIsNone(await runtime.handle_event(event))

        self.assertEqual(transport.requests, [])
        self.assertEqual(len(context.calls), 1)
        self.assertNotIn("credential", context.calls[0][1].chain[0].text.lower())
        await runtime.terminate()


if __name__ == "__main__":
    unittest.main()
