"""AstrBot host checks against the real CoreRuntime and bundled module."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.bundled import (
    BundledExtensionError,
    BundledExtensionInstallation,
)
from ygl_test_subject.adapters.astrbot.command_bridge import (
    AstrBotCommandBridge,
    CommandInvocation,
)
from ygl_test_subject.adapters.astrbot.message_port import AstrBotMessagePort
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.api.administration import AdminOperation
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
from ygl_test_subject.api.services import (
    CapabilityHealth,
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
    HealthStatus,
    HttpResponse,
    ModuleHandlers,
    SecretMaterial,
)
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
    CoreRuntime,
    CoreRuntimeCleanupPending,
    HostIngress,
    TrustedSubscriptionGate,
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
        self.health_resolver = self
        self.close_calls = 0

    def current(self, _module_id, _capability_id):
        return CapabilityHealth(HealthStatus.UNKNOWN), 0

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
        config=None,
        core_factory=CoreRuntime,
    ) -> AstrBotRuntime:
        return AstrBotRuntime(
            context,
            plugin_root=self.plugin_root,
            data_dir=self.data_dir,
            plain_factory=_Plain,
            chain_factory=lambda components: _MessageChain(list(components)),
            http_transport_factory=http_transport_factory,
            config=config,
            raw_legacy_config={
                k: v for k, v in (config or {}).items() if k.startswith("ff14_")
            },
            core_factory=core_factory,
        )

    async def test_public_web_real_core_and_bundled_three_queries_are_offline(self):
        from tests.host.test_ff14_pages import _host_contracts, _request
        from tests.modules.ff14.test_calendar import _event, _ics
        from tests.modules.ff14.test_fflogs import _character_responses

        class Transport(_ItemReplyTransport):
            def __init__(self):
                super().__init__()
                self.logs = deque(_character_responses())
                self.exchanges = 0
                self._credential_channel = object()

            async def request(self, request):
                if request.source_id == "fflogs_public_global":
                    self.requests.append(request)
                    return HttpResponse(
                        200, {}, json.dumps(self.logs.popleft()).encode()
                    )
                if request.source_id.startswith("ff14_calendar"):
                    self.requests.append(request)
                    tomorrow = datetime.now(UTC) + timedelta(days=1)
                    raw = _ics(
                        _event(
                            "synthetic",
                            "DTSTART:" + tomorrow.strftime("%Y%m%dT%H%M%SZ"),
                            "DTEND:"
                            + (tomorrow + timedelta(hours=1)).strftime(
                                "%Y%m%dT%H%M%SZ"
                            ),
                            "SUMMARY:Synthetic event",
                        )
                    )
                    return HttpResponse(200, {"Content-Type": "text/calendar"}, raw)
                return await super().request(request)

            async def request_credential_exchange(self, request):
                self.exchanges += 1
                return HttpResponse(
                    200,
                    {},
                    b'{"access_token":"synthetic-token","token_type":"Bearer","expires_in":3600}',
                )

        class Admin:
            adapter_id, request_id, session_id = "synthetic", "synthetic", "synthetic"

        admin = Admin()

        def core_factory(**kwargs):
            kwargs["admin_context_validator"] = lambda _op, _inv, context, _gen: (
                context is admin
            )
            return CoreRuntime(**kwargs)

        context, transport = _Context(), Transport()
        runtime = self._runtime(
            context,
            config={"web_public_origin": "https://ui.test"},
            http_transport_factory=lambda: transport,
            core_factory=core_factory,
        )
        web, _ = _host_contracts()
        with patch(
            "ygl_test_subject.adapters.astrbot.runtime.EnvironmentKeyProvider.get_key",
            return_value=bytes(range(32)),
        ):
            try:
                await runtime.initialize()
                core = runtime.core_runtime
                await core.admin_credential_repository.bootstrap(bytes(range(32)))
                snapshot = await core.config_repository.current(
                    ConfigTarget(PLUGIN_NAME, "ff14/ff14")
                )
                fields = (
                    core.extension_runtime.candidate("ff14")
                    .package.manifest.modules[0]
                    .config_fields
                )
                await core.admin_operations.update_config(
                    None,
                    "ff14/ff14",
                    ConfigPatch(
                        snapshot.revision,
                        (
                            ConfigFieldUpdate(
                                "credential_fflogs_global",
                                ConfigPatchMode.REPLACE,
                                secret=SecretMaterial(
                                    b'{"schema":1,"client_id":"synthetic-id","client_secret":"synthetic-secret"}'
                                ),
                            ),
                        ),
                        declared_fields=fields,
                    ),
                    authorization=admin,
                )
                for endpoint, body in (
                    ("items", {"query": "100"}),
                    (
                        "character",
                        {
                            "region": "global",
                            "server": "Cerberus",
                            "character": "Synthetic Hero",
                        },
                    ),
                    ("calendar", {"region": "global", "days": 7, "timezone": "UTC"}),
                ):
                    with self.subTest(endpoint=endpoint):
                        request = _request(web, endpoint, body=body)
                        state = runtime.begin_public_web(
                            request, endpoint, request.path
                        )
                        try:
                            from ygl_test_subject.adapters.astrbot.web_public import (
                                query_parameters,
                            )

                            result = await runtime.invoke_public_web(
                                state,
                                endpoint,
                                query_parameters(endpoint, json.dumps(body).encode()),
                            )
                        finally:
                            runtime.finish_public_web(state)
                        self.assertIn(
                            result["status"],
                            ("success", "partial_success", "needs_selection"),
                            result,
                        )
                        self.assertIsNotNone(result["document"])
                self.assertEqual(transport.exchanges, 1)
                self.assertEqual(context.calls, [])
                self.assertFalse(runtime._web_validator._requests)
                counts = await core.database.executor.run_read(
                    lambda connection: tuple(
                        connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                        for name in ("b04_subscriptions", "b04_delivery_events")
                    )
                )
                self.assertEqual(counts, (0, 0))
            finally:
                await runtime.terminate()

    async def test_public_web_same_bearer_budget_and_terminate_revoke_before_lock(self):
        from tests.host.test_ff14_pages import _host_contracts, _request, _token

        web, _ = _host_contracts()
        entered, release = asyncio.Event(), asyncio.Event()

        class Transport(_ItemReplyTransport):
            async def request(self, request):
                entered.set()
                await release.wait()
                return await super().request(request)

        runtime = self._runtime(
            _Context(),
            config={"web_public_origin": "https://ui.test"},
            http_transport_factory=Transport,
        )
        states, tasks = [], []
        try:
            await runtime.initialize()
            token = _token()
            for _ in range(3):
                request = _request(web, token=token)
                states.append(runtime.begin_public_web(request, "items", request.path))
            keys = [state[0]._requests[state[1]].key for state in states]
            self.assertEqual(len(set(keys)), 1)
            tasks = [
                asyncio.create_task(
                    runtime.invoke_public_web(state, "items", {"query": "100"})
                )
                for state in states[:2]
            ]
            await entered.wait()
            await asyncio.sleep(0)
            rejected = await runtime.invoke_public_web(
                states[2], "items", {"query": "100"}
            )
            self.assertEqual(rejected["status"], "error")
            self.assertEqual(rejected["error"]["code"], "rate_limited")
            release.set()
            results = await asyncio.gather(*tasks)
            self.assertTrue(
                all(
                    result["status"]
                    in ("success", "partial_success", "needs_selection")
                    for result in results
                ),
                results,
            )
            old_validator, old_core = runtime._web_validator, runtime.core_runtime
            request = _request(web, token=token)
            state = runtime.begin_public_web(request, "items", request.path)
            states.append(state)
            proof = old_validator.mint(state[1])
            binding = old_validator.consume(
                proof, module_id="ff14/ff14", capability_id="item.lookup", generation=7
            )
            await runtime._lock.acquire()
            stopping = asyncio.create_task(runtime.terminate())
            await asyncio.sleep(0)
            self.assertFalse(old_validator.is_current(proof, binding))
            self.assertFalse(runtime.ready)
            runtime._lock.release()
            await stopping
            await runtime.initialize()
            self.assertIsNot(runtime.core_runtime, old_core)
            request = _request(web, token=token)
            new_state = runtime.begin_public_web(request, "items", request.path)
            states.append(new_state)
            self.assertNotEqual(
                runtime._web_validator._requests[new_state[1]].key, keys[0]
            )
            self.assertFalse(runtime._web_validator.is_current(proof, binding))
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            for state in states:
                runtime.finish_public_web(state)
            await runtime.terminate()

    async def test_trusted_gate_binding_comes_from_one_scan_and_initializes_installed_sha(
        self,
    ):
        with patch(
            "ygl_test_subject.adapters.astrbot.runtime.discover_packages",
            wraps=discover_packages,
        ) as scan:
            expected, gates = AstrBotRuntime._bundled_manifest_expectations(
                self.plugin_root
            )
            scan.assert_called_once()
        gate = gates["ff14/ff14"]
        self.assertIsInstance(gate, TrustedSubscriptionGate)
        self.assertIs(gate.manifest, expected["ff14/ff14"])
        self.assertEqual(gate.field, "ff14_subscriptions_enabled")
        with self.assertRaises(TypeError):
            gates["other/mod"] = gate
        runtime = self._runtime(_Context(), http_transport_factory=_IdleTransport)
        try:
            await runtime.initialize()
            core = runtime.core_runtime
            candidate = core.extension_runtime.candidate("ff14").package
            packaged = discover_packages(self.plugin_root / "modules")[0]
            self.assertNotEqual(
                packaged._provenance.root_locator, candidate._provenance.root_locator
            )
            self.assertEqual(
                candidate._provenance.manifest_sha256, gate.manifest_sha256
            )
            snapshot = await core.config_repository.current(
                ConfigTarget(PLUGIN_NAME, "ff14/ff14")
            )
            self.assertIs(snapshot.values[gate.field], True)
            connection = core.database.connect()
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT phase FROM subscription_gate_bootstrap"
                    ).fetchone()[0],
                    "complete",
                )
            finally:
                connection.close()
            self.assertTrue(
                (await runtime.public_ff14_page_state())["subscription_gate"][
                    "supported"
                ]
            )
        finally:
            await runtime.terminate()

    async def test_untrusted_bundle_never_initializes_gate_bootstrap(self):
        runtime = self._runtime(_Context(), http_transport_factory=_IdleTransport)
        installation = BundledExtensionInstallation(
            self.data_dir / "untrusted",
            self.data_dir / "untrusted/ff14",
            False,
            False,
            "existing_manifest_mismatch",
        )
        with patch(
            "ygl_test_subject.adapters.astrbot.runtime.install_bundled_ff14",
            return_value=installation,
        ):
            try:
                await runtime.initialize()
                connection = runtime.core_runtime.database.connect()
                try:
                    self.assertEqual(
                        connection.execute(
                            "SELECT phase FROM subscription_gate_bootstrap"
                        ).fetchone()[0],
                        "pending",
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM config_entries"
                        ).fetchone()[0],
                        0,
                    )
                finally:
                    connection.close()
            finally:
                await runtime.terminate()

    async def test_invalid_raw_migration_stays_incomplete_and_reports_safe_field(self):
        context = _Context()
        for config in (
            {"ff14_calendar_default_days": 31},
            {"ff14_calendar_default_timezone": "private-invalid-zone"},
        ):
            runtime = self._runtime(
                context, config=config, http_transport_factory=_IdleTransport
            )
            await runtime.initialize()
            self.assertFalse(runtime.ready)
            self.assertTrue(runtime.core_runtime.configuration_blocked)
            self.assertFalse(runtime.core_runtime.started)
            self.assertIsNone(runtime.core_runtime._pump_task)
            event = _Event()
            event.message = "/ygl help"
            text = await runtime.handle_event(event)
            self.assertIn("Core 配置管理", text)
            self.assertIn("尚未启动", text)
            self.assertNotIn("private-invalid-zone", text)
            await runtime.terminate()
        self.assertEqual(context.calls, [])

    async def test_trusted_raw_migrates_to_core_and_ff14_without_host_injection(self):
        config = {
            "ff14_default_region": "global",
            "ff14_calendar_default_days": 3,
            "ff14_calendar_default_timezone": "UTC",
            "ff14_calendar_default_delivery_time": "13:25",
        }
        runtime = self._runtime(
            _Context(), config=config, http_transport_factory=_IdleTransport
        )
        # A later host mutation cannot alter this instance or prevent startup.
        config["ff14_calendar_default_days"] = 31
        await runtime.initialize()
        try:
            core = runtime.core_runtime
            services = core.module_services.for_module("ff14/ff14")
            snapshot = await services.config.current()
            self.assertEqual(snapshot.values["ff14_calendar_default_days"], 3)
            module = core.registry.snapshot().module("ff14/ff14")
            query = module.handlers.capabilities["ff14.calendar.query"]
            subscribe = module.handlers.capabilities[
                "ff14.calendar.subscription.create"
            ]
            self.assertIsNone(query._config)
            self.assertIsNone(subscribe._config)
            self.assertEqual(
                snapshot.values["core_defaults"]["default_region"], "global"
            )
            stored = await core.repositories.config.current(snapshot.target)
            self.assertEqual(stored.values["ff14_calendar_default_days"], 3)
            self.assertNotIn("ff14_default_region", stored.values)
            self.assertTrue(await core.ordinary_config_migration.complete())
            event = _Event()
            event.message = "/ygl ff14 calendar help"
            help_text = await runtime.handle_event(event)
            self.assertIn("默认区域提示：global", help_text)
            self.assertIn("具体规则见模块帮助", help_text)
        finally:
            await runtime.terminate()

    async def test_all_catalog_help_projections_read_core_defaults_without_dispatch(
        self,
    ):
        context = _Context()
        runtime = self._runtime(
            context,
            config={"ff14_default_region": "global"},
            http_transport_factory=_IdleTransport,
        )
        await runtime.initialize()
        try:
            event = _Event()
            for text in (
                "/ygl",
                "/ygl help",
                "/ygl ff14",
                "/ygl ff14 help",
                "/ygl ff14 market help",
                "/ygl ff14 calendar help",
                "/ygl ff14 calendar subscribe help",
            ):
                with self.subTest(text=text):
                    event.message = text
                    response = await runtime.handle_event(event)
                    self.assertIn("默认区域提示：global", response)
                    self.assertIn("帮助", response)
            for text in ('/ygl ff14 calendar "unclosed', '/ygl "invalid route" help'):
                with self.subTest(text=text):
                    event.message = text
                    response = await runtime.handle_event(event)
                    self.assertIn("用法", response)
                    self.assertNotIn("默认区域提示", response)
            for text in (
                "/ygl missing",
                "/ygl ff14 absent",
                "/ygl ff14 calendar",
                "/ygl ff14 calendar cn days=bad",
            ):
                event.message = text
                response = await runtime.handle_event(event)
                self.assertIn("help", response)
                self.assertNotIn("默认区域提示", response)
                self.assertNotIn("/ygl ff14 market", response)
            event.message = "/ygl help"
            root_help = await runtime.handle_event(event)
            self.assertIn("/ygl ff14 help", root_help)
            self.assertNotIn("/ygl ff14 market", root_help)
            self.assertNotIn("ygo", root_help)
            event.message = "/ygl ff14 help"
            module_help = await runtime.handle_event(event)
            self.assertIn("market help", module_help)
            self.assertIn("logs help", module_help)
            self.assertIn("需对应区域凭据", module_help)
            self.assertIn("当前不可用", module_help)
            self.assertNotIn("server_hint=", module_help)
            event.message = "/ygl ff14 logs help"
            details = await runtime.handle_event(event)
            self.assertIn("server_hint=", details)
            self.assertEqual(context.calls, [])
        finally:
            await runtime.terminate()

    async def test_manifest_semantic_validators_manage_read_repair_and_marker_restart(
        self,
    ):
        from ygl_test_subject.infrastructure.sqlite.repositories_config_migration import (
            SQLiteOrdinaryConfigurationMigrationRepository,
        )
        from ygl_test_subject.services.configuration import ConfigurationValueError
        from ygl_test_subject.services.core_configuration import (
            CORE_CONFIG_FIELDS,
            CORE_MODULE_ID,
        )

        class Admin:
            adapter_id, request_id, session_id = "synthetic", "synthetic", "synthetic"

        admin = Admin()

        def factory(**kwargs):
            kwargs["admin_context_validator"] = (
                lambda _op, _inv, context, _gen: context is admin
            )
            return CoreRuntime(**kwargs)

        runtime = self._runtime(
            _Context(),
            config={"ff14_default_region": "global"},
            http_transport_factory=_IdleTransport,
            core_factory=factory,
        )
        await runtime.initialize()
        core = runtime.core_runtime
        await core.admin_credential_repository.bootstrap(bytes(range(32)))
        target = ConfigTarget(PLUGIN_NAME, "ff14/ff14")
        module = core.registry.snapshot().module("ff14/ff14")
        fields = module.manifest.config_fields
        self.assertTrue(
            all(
                field.group == "calendar"
                for field in fields
                if field.name.startswith("ff14_calendar_default_")
            )
        )
        for field, value in (
            ("ff14_calendar_default_timezone", "private-zone"),
            ("ff14_calendar_default_delivery_time", "24:00"),
        ):
            snapshot = await core.config_repository.current(target)
            with self.assertRaises(ConfigurationValueError):
                await core.admin_facade.update_config(
                    None,
                    "ff14/ff14",
                    ConfigPatch(
                        snapshot.revision,
                        (
                            ConfigFieldUpdate(
                                field, ConfigPatchMode.REPLACE, value=value
                            ),
                        ),
                        fields,
                    ),
                    authorization=admin,
                )
            self.assertEqual(await core.config_repository.current(target), snapshot)
        config_repository = SQLiteOrdinaryConfigurationMigrationRepository(
            core.config_repository
        )
        snapshot = await core.config_repository.current(target)
        await core.database.executor.run_transaction(
            lambda unit: config_repository._write(
                unit,
                target,
                "ff14_calendar_default_timezone",
                "private-zone",
                snapshot.revision,
            )
        )
        with self.assertRaises(ConfigurationValueError):
            await core.module_services.for_module("ff14/ff14").config.current()
        await core.admin_facade.update_config(
            None,
            "ff14/ff14",
            ConfigPatch(
                snapshot.revision,
                (
                    ConfigFieldUpdate(
                        "ff14_calendar_default_timezone",
                        ConfigPatchMode.REPLACE,
                        value="UTC",
                    ),
                ),
                fields,
            ),
            authorization=admin,
        )
        self.assertEqual(
            (
                await core.module_services.for_module("ff14/ff14").config.current()
            ).values["ff14_calendar_default_timezone"],
            "UTC",
        )
        summary = await core.admin_facade.config_snapshot(
            None, CORE_MODULE_ID, authorization=admin
        )
        await core.admin_facade.update_config(
            None,
            CORE_MODULE_ID,
            ConfigPatch(
                summary.revision,
                (ConfigFieldUpdate("default_region", ConfigPatchMode.CLEAR),),
                CORE_CONFIG_FIELDS,
            ),
            authorization=admin,
        )
        self.assertEqual(
            (await core.core_defaults.current()).values["default_region"], "cn"
        )
        await runtime.terminate()
        # Completed startup must not parse the absent preparation file again.
        runtime._raw_legacy_config = None
        await runtime.initialize()
        try:
            self.assertTrue(runtime.ready)
            self.assertEqual(
                (await runtime.core_runtime.core_defaults.current()).values[
                    "default_region"
                ],
                "cn",
            )
        finally:
            await runtime.terminate()

    async def _assert_current_config_repair_recovers_commands(self, mode, region):
        from ygl_test_subject.infrastructure.sqlite.repositories_config_migration import (
            SQLiteOrdinaryConfigurationMigrationRepository,
        )
        from ygl_test_subject.services.core_configuration import (
            CORE_CONFIG_FIELDS,
            CORE_MODULE_ID,
            core_config_target,
        )

        from tests.host.test_market_integration import FixtureTransport

        class Admin:
            adapter_id, request_id, session_id = "synthetic", "synthetic", "synthetic"

        admin = Admin()

        def factory(**kwargs):
            kwargs["admin_context_validator"] = (
                lambda _op, _inv, context, _gen: context is admin
            )
            return CoreRuntime(**kwargs)

        context = _Context()
        transport = FixtureTransport()
        runtime = self._runtime(
            context, http_transport_factory=lambda: transport, core_factory=factory
        )
        await runtime.initialize()
        try:
            core = runtime.core_runtime
            await core.admin_credential_repository.bootstrap(bytes(range(32)))
            target = core_config_target(PLUGIN_NAME)
            before = await core.config_repository.current(target)
            repository = SQLiteOrdinaryConfigurationMigrationRepository(
                core.config_repository
            )
            await core.database.executor.run_transaction(
                lambda unit: repository._write(
                    unit, target, "default_region", "synthetic-invalid", before.revision
                )
            )
            state = await runtime.public_ff14_page_state(include_values=True)
            self.assertEqual(
                state["ordinary_config"]["invalid_field"], "default_region"
            )
            self.assertIsNone(state["ordinary_config"]["values"])
            self.assertTrue(runtime.ready)
            event = _Event()
            event.message = "/ygl ff14 calendar help"
            rejected = await runtime.handle_event(event)
            self.assertIn("普通配置无效", rejected)
            self.assertEqual(context.calls, [])
            # Invalid defaults refuse a price query; public catalog IO precedes config.
            event.message = "/ygl ff14 market 44091"
            self.assertIsNone(await runtime.handle_event(event))
            self.assertEqual(
                [request.path for request in transport.requests],
                ["/api/v2/worlds", "/api/v2/data-centers"],
            )
            self.assertIn("request failed", context.calls[-1][1].chain[0].text)
            self.assertNotIn("synthetic-invalid", context.calls[-1][1].chain[0].text)
            summary = await core.admin_facade.config_snapshot(
                None, CORE_MODULE_ID, authorization=admin
            )
            update = (
                ConfigFieldUpdate("default_region", mode, value=region)
                if mode is ConfigPatchMode.REPLACE
                else ConfigFieldUpdate("default_region", mode)
            )
            await core.admin_facade.update_config(
                None,
                CORE_MODULE_ID,
                ConfigPatch(summary.revision, (update,), CORE_CONFIG_FIELDS),
                authorization=admin,
            )
            # No page read or restart between authorized repair and the command.
            event.message = "/ygl ff14 calendar help"
            help_text = await runtime.handle_event(event)
            self.assertIn(f"默认区域提示：{region}", help_text)
            self.assertTrue(runtime.ready)
            event.message = "/ygl ff14 status"
            self.assertIsNone(await runtime.handle_event(event))
            self.assertTrue(context.calls)
            # Read the repaired Core default in the registered market handler.
            event.message = "/ygl ff14 market 44091"
            self.assertIsNone(await runtime.handle_event(event))
            queried_regions = {
                request.path.split("/")[-2]
                for request in transport.requests
                if "/aggregated/" in request.path
            }
            self.assertEqual(
                queried_regions,
                {"China"}
                if region == "cn"
                else {"North-America", "Europe", "Japan", "Oceania"},
            )
            # Help/repair grants no new admission: a forged Host event does no IO.
            calls, requests = len(context.calls), len(transport.requests)
            event.sender = ""
            self.assertIn("来源", await runtime.handle_event(event))
            self.assertEqual(
                (len(context.calls), len(transport.requests)), (calls, requests)
            )
        finally:
            await runtime.terminate()

    async def test_commands_recover_after_page_invalid_config_replace_without_page_read(
        self,
    ):
        await self._assert_current_config_repair_recovers_commands(
            ConfigPatchMode.REPLACE, "global"
        )

    async def test_commands_recover_after_page_invalid_config_clear_without_page_read(
        self,
    ):
        await self._assert_current_config_repair_recovers_commands(
            ConfigPatchMode.CLEAR, "cn"
        )

    async def test_real_composition_missing_prepared_raw_input_fails_closed(self):
        runtime = self._runtime(_Context(), http_transport_factory=_IdleTransport)
        runtime._raw_legacy_config = None
        await runtime.initialize()
        self.assertFalse(runtime.ready)
        self.assertTrue(runtime.core_runtime.configuration_blocked)
        self.assertFalse(runtime.core_runtime.started)
        self.assertIsNone(runtime.core_runtime._pump_task)
        await runtime.terminate()

    async def test_completed_migration_ignores_invalid_legacy_and_preserves_existing_subscription(
        self,
    ):
        context = _Context()
        config = {
            "ff14_calendar_default_timezone": "UTC",
            "ff14_calendar_default_delivery_time": "13:25",
        }
        runtime = self._runtime(
            context, config=config, http_transport_factory=_IdleTransport
        )
        await runtime.initialize()
        event = _Event("FriendMessage")
        event.message = "/ygl ff14 calendar subscribe cn"
        try:
            await runtime.handle_event(event)
            self.assertTrue(context.calls)
            self.assertIn("13:25", context.calls[-1][1].chain[0].text)
        finally:
            await runtime.terminate()
        rejected = self._runtime(
            context,
            config={"ff14_calendar_default_days": 31},
            http_transport_factory=_IdleTransport,
        )
        await rejected.initialize()
        self.assertTrue(rejected.ready)
        self.assertTrue(
            await rejected.core_runtime.ordinary_config_migration.complete()
        )
        self.assertEqual(
            (
                await rejected.core_runtime.config_repository.current(
                    ConfigTarget(PLUGIN_NAME, "ff14/ff14")
                )
            ).values["ff14_calendar_default_timezone"],
            "UTC",
        )
        await rejected.terminate()
        restored = self._runtime(
            context,
            config={
                "ff14_calendar_default_timezone": "Asia/Tokyo",
                "ff14_calendar_default_delivery_time": "18:00",
            },
            http_transport_factory=_IdleTransport,
        )
        await restored.initialize()
        event.message = "/ygl ff14 calendar subscriptions"
        try:
            await restored.handle_event(event)
            text = context.calls[-1][1].chain[0].text
            self.assertIn("UTC", text)
            self.assertIn("13:25", text)
            self.assertNotIn("18:00", text)
        finally:
            await restored.terminate()

    async def test_old_initialize_cannot_publish_after_terminate_starts(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Core:
            registry = Registry()

            async def start(self):
                entered.set()
                await release.wait()

            async def close(self):
                return True

        runtime = self._runtime(
            _Context(),
            http_transport_factory=_IdleTransport,
        )
        runtime._core_factory = lambda **_: Core()
        start = asyncio.create_task(runtime.initialize())
        await entered.wait()
        close = asyncio.create_task(runtime.terminate())
        await asyncio.sleep(0)
        self.assertFalse(runtime.ready)
        release.set()
        await start
        self.assertFalse(runtime.ready)
        self.assertIsNone(runtime._bridge)
        await close
        self.assertIsNone(runtime.core_runtime)

    async def test_public_projection_fences_core_replacement_readiness_and_close(self):
        from types import SimpleNamespace

        from ygl_test_subject.core.ports import SubscriptionGateState

        for change in ("replace", "ready", "close"):
            with self.subTest(change=change):
                entered, release = asyncio.Event(), asyncio.Event()

                async def blocked(_):
                    entered.set()
                    await release.wait()
                    return SubscriptionGateState(True, True, True, None)

                runtime = self._runtime(_Context())
                runtime._core = SimpleNamespace(subscription_gate_state=blocked)
                runtime._ready = True
                task = asyncio.create_task(runtime.public_ff14_page_state())
                await entered.wait()
                if change == "replace":
                    runtime._core = None
                elif change == "ready":
                    runtime._ready = False
                else:
                    runtime._generation += 1
                    runtime._closing = True
                release.set()
                with self.assertRaisesRegex(RuntimeError, "generation changed"):
                    await task

    async def test_public_page_projection_ready_uses_registry_but_never_secrets(self):
        runtime = self._runtime(_Context(), http_transport_factory=_IdleTransport)
        await runtime.initialize()
        try:
            core = runtime.core_runtime
            with patch.object(
                type(core.module_services),
                "for_module",
                side_effect=AssertionError("page read module services"),
            ):
                overview = await runtime.public_ff14_page_state()
                settings = await runtime.public_ff14_page_state(include_values=True)
            self.assertEqual(overview["runtime"], {"state": "ready", "reason": None})
            self.assertEqual(overview["module"], {"registered": True, "enabled": True})
            self.assertNotIn("values", overview["ordinary_config"])
            self.assertEqual(
                settings["ordinary_config"]["values"],
                dict(runtime._config_snapshot.as_values()),
            )
            for item in overview["credentials"].values():
                self.assertEqual(item, {"configured": None, "state": "unknown"})
            self.assertEqual(
                overview["subscription_gate"],
                {"supported": True, "enabled": True, "can_run": True, "reason": None},
            )
            self.assertEqual(len(overview["sources"]), 7)
            self.assertTrue(
                all(
                    item["declared"] is True
                    and item["freshness"] == "unknown"
                    and item["last_success_at"] is None
                    for item in overview["sources"]
                )
            )
            self.assertEqual(
                set(overview),
                {
                    "schema_version",
                    "runtime",
                    "module",
                    "ordinary_config",
                    "credentials",
                    "subscription_gate",
                    "sources",
                },
            )
            settings["ordinary_config"]["values"]["ff14_calendar_default_days"] = 20
            self.assertEqual(
                (await runtime.public_ff14_page_state(include_values=True))[
                    "ordinary_config"
                ]["values"]["ff14_calendar_default_days"],
                7,
            )
        finally:
            await runtime.terminate()

    async def test_catalog_real_ff14_is_metadata_only_and_never_invokes(self):
        context = _Context()
        runtime = self._runtime(context, http_transport_factory=_IdleTransport)
        self.assertIsNone(runtime.public_module_catalog()["modules"])
        await runtime.initialize()
        try:
            core = runtime.core_runtime
            with (
                patch.object(
                    type(core.repositories.config),
                    "current",
                    side_effect=AssertionError("catalog read config values"),
                ),
                patch.object(
                    type(core.module_services),
                    "for_module",
                    side_effect=AssertionError("catalog invoked module"),
                ),
            ):
                catalog = runtime.public_module_catalog()
            self.assertEqual(catalog["runtime"], {"state": "ready"})
            self.assertEqual(len(catalog["modules"]), 1)
            module = catalog["modules"][0]
            self.assertEqual(catalog["schema_version"], 1)
            self.assertEqual(
                set(catalog),
                {"schema_version", "catalog_revision", "runtime", "modules"},
            )
            self.assertEqual(module["module_id"], "ff14/ff14")
            self.assertEqual(module["state"], "loaded")
            self.assertEqual(
                set(module),
                {
                    "module_id",
                    "route",
                    "category",
                    "version",
                    "enabled",
                    "lifecycle",
                    "state",
                    "reason",
                    "capabilities",
                    "config_fields",
                    "pages",
                    "resources",
                    "module_epoch",
                    "runtime_id",
                    "asset_version",
                },
            )
            self.assertTrue(
                all(
                    set(item) == {"name", "required"}
                    for item in module["config_fields"]
                )
            )
            self.assertTrue(module["pages"])
            self.assertTrue(module["resources"])
            for page in module["pages"]:
                self.assertEqual(
                    set(page),
                    {
                        "route_id",
                        "title",
                        "order",
                        "access",
                        "capability_id",
                        "entry",
                        "styles",
                    },
                )
            for resource in module["resources"]:
                self.assertEqual(set(resource), {"path", "sha256"})
            self.assertNotIn("can_invoke", json.dumps(catalog))
            catalog["modules"].clear()
            self.assertEqual(len(runtime.public_module_catalog()["modules"]), 1)
            self.assertEqual(context.calls, [])
            project = core.public_module_catalog

            def changes_generation():
                data = project()
                runtime._generation += 1
                return data

            with patch.object(core, "public_module_catalog", changes_generation):
                with self.assertRaisesRegex(RuntimeError, "generation changed"):
                    runtime.public_module_catalog()
        finally:
            await runtime.terminate()
        self.assertEqual(
            runtime.public_module_catalog()["runtime"], {"state": "not_ready"}
        )
        self.assertIsNone(runtime.public_module_catalog()["modules"])

    async def test_catalog_invalid_configuration_is_unknown_directory(self):
        runtime = self._runtime(_Context(), config={"ff14_calendar_default_days": 0})
        await runtime.initialize()
        self.assertEqual(
            runtime.public_module_catalog(),
            {
                "schema_version": 1,
                "catalog_revision": None,
                "runtime": {"state": "invalid_config"},
                "modules": None,
            },
        )
        await runtime.terminate()

    async def test_public_page_projection_invalid_config_is_recoverable_without_core(
        self,
    ):
        runtime = self._runtime(_Context(), config={"ff14_calendar_default_days": 0})
        await runtime.initialize()
        data = await runtime.public_ff14_page_state(include_values=True)
        self.assertEqual(
            data["runtime"],
            {"state": "invalid_config", "reason": "ordinary_config_invalid"},
        )
        self.assertEqual(
            data["ordinary_config"],
            {
                "state": "invalid",
                "invalid_field": "ff14_calendar_default_days",
                "values": None,
            },
        )
        self.assertEqual(data["module"], {"registered": None, "enabled": None})
        self.assertTrue(all(item["declared"] is None for item in data["sources"]))
        self.assertTrue(runtime.core_runtime.configuration_blocked)
        self.assertFalse(runtime.core_runtime.started)
        await runtime.terminate()

    async def test_public_projection_registry_failure_is_unknown_not_missing(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        runtime = self._runtime(_Context())
        self.assertEqual(
            (await runtime.public_ff14_page_state())["ordinary_config"]["state"],
            "unknown",
        )
        registry = Mock()
        registry.snapshot.side_effect = RuntimeError("private runtime path")
        runtime._core = SimpleNamespace(registry=registry)
        data = await runtime.public_ff14_page_state()
        self.assertEqual(data["module"], {"registered": None, "enabled": None})
        self.assertTrue(all(item["declared"] is None for item in data["sources"]))
        registry.snapshot.side_effect = None
        registry.snapshot.return_value = SimpleNamespace(modules={})
        data = await runtime.public_ff14_page_state()
        self.assertEqual(data["module"], {"registered": False, "enabled": False})
        self.assertTrue(all(item["declared"] is False for item in data["sources"]))

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
        from tests.fixtures.admin_authorization import native_grant

        grant = await native_grant(credentials, AdminOperation.SET_ENABLED)
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
        self.assertEqual(runtime.bundle_failure_reason, "existing_manifest_mismatch")
        for message in ("/ygl help", "/ygl ff14 status"):
            event = _Event()
            event.message = message
            diagnostic = await runtime.handle_event(event)
            self.assertIn("FF14 内置模块未能安全加载", diagnostic)
            self.assertIn("[existing_manifest_mismatch]", diagnostic)
            self.assertIn("清单与发行包不一致", diagnostic)
            self.assertIn("重载插件", diagnostic)
            self.assertNotIn(str(self.data_dir), diagnostic)
        await runtime.terminate()

    async def test_bundle_failure_reasons_have_safe_distinct_recovery_actions(self):
        cases = (
            ("package_root_unavailable", "暂存目录", "长路径支持"),
            ("package_permission_denied", "访问被拒绝", "目录权限"),
            ("extension_root_permission_denied", "访问被拒绝", "目录权限"),
            ("unknown-private-path-and-secret", "未能安全确认", "宿主安装日志"),
        )
        for reason, category, action in cases:
            with self.subTest(reason=reason):
                context = _Context()
                runtime = self._runtime(context, http_transport_factory=_IdleTransport)
                outcome = BundledExtensionInstallation(
                    self.data_dir / "untrusted",
                    self.data_dir / "untrusted" / "ff14",
                    False,
                    False,
                    reason,
                )
                with patch(
                    "ygl_test_subject.adapters.astrbot.runtime.install_bundled_ff14",
                    return_value=outcome,
                ):
                    await runtime.initialize()
                try:
                    reply = await runtime.handle_event(_Event())
                    self.assertIn(category, reply)
                    self.assertIn(action, reply)
                    self.assertNotIn("当前没有已注册模块", reply)
                    self.assertNotIn(str(self.data_dir), reply)
                    self.assertNotIn("unknown-private-path-and-secret", reply)
                    self.assertEqual(context.calls, [])
                finally:
                    await runtime.terminate()

    async def test_unsupported_bundle_environment_is_visible_on_startup_and_help(self):
        runtime = self._runtime(_Context(), http_transport_factory=_IdleTransport)
        with patch(
            "ygl_test_subject.adapters.astrbot.runtime.install_bundled_ff14",
            side_effect=BundledExtensionError("unsupported_environment"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                await runtime.initialize()
        self.assertIn("[unsupported_environment]", str(raised.exception))
        self.assertIn("Windows/Python", str(raised.exception))
        self.assertIn("重启宿主", str(raised.exception))
        self.assertNotIn(str(self.plugin_root), str(raised.exception))
        self.assertFalse(runtime.ready)
        self.assertIsNone(runtime.core_runtime)
        event = _Event()
        event.message = "/ygl help"
        self.assertEqual(await runtime.handle_event(event), str(raised.exception))
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
        self.assertIn("模块当前不可用", response)
        self.assertEqual(handler.calls, 0)
        self.assertEqual(context.calls, [])

        # The bundled module is active and has a genuine OWNER declaration.
        event.message = "/ygl ff14 calendar subscriptions"
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

    async def test_page_item_copy_command_parses_the_exact_item_query(self):
        runtime = self._runtime(_Context(), http_transport_factory=_IdleTransport)
        await runtime.initialize()
        try:
            event = _Event()
            event.message = "/ygl ff14 item 44091"
            action = AstrBotCommandBridge(runtime.core_runtime.registry).parse(event)
            self.assertIsInstance(action, CommandInvocation)
            self.assertEqual(action.module_id, "ff14/ff14")
            self.assertEqual(action.operation_path, "item")
            self.assertEqual(dict(action.parameters), {"query": "44091"})
        finally:
            await runtime.terminate()

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
