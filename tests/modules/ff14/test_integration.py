from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.command_bridge import (
    AstrBotCommandBridge,
    CommandInvocation,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import MessageReceipt, MessageStatus
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.infrastructure.http import TransportRequest
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.modules.ff14.assembly import SUPPORT
from ygl_test_subject.services.admin_authorization import _digest
from ygl_test_subject.services.core_runtime import (
    CoreRuntime,
    HostIngress,
    TrustedSubscriptionGate,
)
from ygl_test_subject.services.source_credentials import SourceCredentialPolicy
from ygl_test_subject.services.trusted_assembly import (
    CapturedAssemblyInventory,
    assemble_reviewed,
)

from yomihime_game_link_sdk.display import (
    DisplayLimits,
    DisplayOutput,
    LinksBlock,
    TextBlock,
)
from yomihime_game_link_sdk.services import CapabilityHealth, HealthStatus, HttpResponse
from yomihime_game_link_sdk.subscriptions import ConversationKind

ROOT = Path(__file__).resolve().parents[3]
MODULE_SOURCE = ROOT / "modules" / "ff14"
GLOBAL_MODULE_ID = "ff14/ff14"
FIXTURE = ROOT / "tests" / "fixtures" / "ff14" / "items.json"
CALENDAR_ICS = (
    b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Tests//FF14//EN\r\nEND:VCALENDAR\r\n"
)


class _Renderer:
    async def render(self, document, *, limits, audience):
        del limits, audience
        lines = [document.title]
        for block in document.ordered_blocks:
            if isinstance(block, TextBlock):
                lines.append(block.text)
            elif isinstance(block, LinksBlock):
                lines.extend(f"{link.label}: {link.url}" for link in block.links)
        return validate_contract(DisplayOutput("\n".join(lines)))

    async def render_batch(self, batch, limits):
        del batch, limits
        raise AssertionError("M1 item/status commands have no digest output")


class _MessagePort:
    def __init__(self) -> None:
        self.calls = []

    async def send(self, target, payload):
        self.calls.append((target, payload))
        return MessageReceipt(MessageStatus.ACCEPTED, "ff14-item-message")


class _FixtureTransport:
    """Offline fixture transport; it does not claim a live source connection."""

    def __init__(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.requests: list[TransportRequest] = []
        self.calendar_ready = asyncio.Event()
        self.credential_exchanges = []

    async def request(self, request: TransportRequest) -> HttpResponse:
        self.requests.append(request)
        if (
            request.source_id == "universalis_market"
            and request.path == "/api/v2/worlds"
        ):
            payload = [{"id": 71001, "name": "Synthetic World"}]
        elif (
            request.source_id == "universalis_market"
            and request.path == "/api/v2/data-centers"
        ):
            payload = [{"name": "SyntheticDC", "region": "China", "worlds": [71001]}]
        elif request.source_id == "xivapi_items" and request.path.endswith("/search"):
            payload = self.fixture["xivcdn"]["search_first_page"]
        elif request.source_id == "xivapi_items" and request.path.endswith("/90001"):
            payload = self.fixture["xivcdn"]["item_detail"]
        elif request.source_id == "garland_items" and request.path.endswith(
            "/90001.json"
        ):
            payload = self.fixture["garland"]["linked_item_detail"]
        elif request.source_id == "ff14_calendar_primary":
            if (
                len(
                    [r for r in self.requests if r.source_id == "ff14_calendar_primary"]
                )
                == 2
            ):
                self.calendar_ready.set()
            return validate_contract(
                HttpResponse(200, {"Content-Type": "text/calendar"}, CALENDAR_ICS)
            )
        else:
            return validate_contract(HttpResponse(404, {}, b""))
        return validate_contract(
            HttpResponse(
                200,
                {"Content-Type": "application/json"},
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        )

    async def request_credential_exchange(self, request):
        self.credential_exchanges.append(request)
        raise AssertionError("FFLogs credentials are not configured in this test")


class _AdminContext:
    adapter_id = "test-adapter"
    request_id = "ff14-m1-item-test"
    session_id = "ff14-m1-item-test-session"


class _TextEvent:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_message_str(self) -> str:
        return self.text


class FF14InstalledModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_manifest_factory_command_http_and_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            extension_root = root / "extensions"
            installed_package = extension_root / "ff14"
            installed_package.parent.mkdir(parents=True)
            shutil.copytree(MODULE_SOURCE, installed_package)

            discovered = discover_packages(extension_root)
            self.assertEqual(len(discovered), 1)
            self.assertTrue(discovered[0].valid, discovered[0].diagnostic)
            package = discovered[0].manifest
            self.assertIsNotNone(package)
            self.assertEqual(package.package_id, "ff14")
            self.assertEqual(package.contract_version, "2.0")
            module_manifest = package.modules[0]
            self.assertEqual(module_manifest.module_id, "ff14")
            self.assertEqual(module_manifest.factory_entry, "module:Factory")
            self.assertEqual(
                tuple(command.operation_path for command in module_manifest.commands),
                (
                    "market",
                    "item",
                    "logs",
                    "calendar",
                    "status",
                    "calendar subscribe",
                    "calendar subscriptions",
                    "calendar update",
                    "calendar cancel",
                ),
            )
            capabilities = {
                capability.capability_id: capability
                for capability in module_manifest.capabilities
            }
            self.assertEqual(
                capabilities["item.lookup"].required_sources, ("xivapi_items",)
            )
            self.assertEqual(
                capabilities["item.lookup"].invocation_policy.value,
                "command_and_public_web",
            )
            self.assertEqual(capabilities["item.lookup"].effect.value, "read_only")
            self.assertEqual(capabilities["item.lookup"].privacy_floor.value, "public")
            self.assertEqual(capabilities["ff14.logs.character"].required_sources, ())
            self.assertEqual(capabilities["ff14.logs.character"].required_config, ())
            self.assertNotIn("ff14.logs.output_percentile", capabilities)
            for action in ("create", "update", "cancel"):
                capability = capabilities[f"ff14.calendar.subscription.{action}"]
                self.assertEqual(capability.privacy_floor.value, "owner")
                self.assertEqual(capability.effect.value, "write")
            list_capability = capabilities["ff14.calendar.subscription.list"]
            self.assertEqual(list_capability.privacy_floor.value, "owner")
            self.assertEqual(list_capability.effect.value, "read_only")
            self.assertEqual(
                {source.source_id: source.host for source in module_manifest.sources},
                {
                    "universalis_market": "universalis.app",
                    "xivapi_items": "xivapi-v2.xivcdn.com",
                    "garland_items": "garlandtools.cn",
                    "fflogs_public_cn": "cn.fflogs.com",
                    "fflogs_public_global": "www.fflogs.com",
                    "fflogs_stats_cn": "cn.fflogs.com",
                    "fflogs_stats_global": "www.fflogs.com",
                    "ff14_calendar_primary": "calendar.google.com",
                    "ff14_calendar_fallback": "p66-caldav.icloud.com",
                },
            )
            self.assertEqual(
                tuple(
                    (tool.name, tool.capability_id, dict(tool.parameter_mapping))
                    for tool in module_manifest.tools
                ),
                (
                    (
                        "ff14_market_query",
                        "ff14.market.query",
                        {
                            "query": "query",
                            "server": "server",
                            "dc": "dc",
                            "region": "region",
                            "quality": "quality",
                            "intent": "intent",
                            "action": "action",
                            "request_language": "request_language",
                            "context_language": "context_language",
                        },
                    ),
                    (
                        "ff14_market_select",
                        "ff14.market.select",
                        {
                            "batch_id": "batch_id",
                            "generation": "generation",
                            "item_id": "item_id",
                            "action": "action",
                            "scope_kind": "scope_kind",
                            "world_id": "world_id",
                            "dc_id": "dc_id",
                        },
                    ),
                    (
                        "ff14_item_lookup",
                        "item.lookup",
                        {"query": "query", "action": "action"},
                    ),
                    (
                        "ff14_item_select",
                        "ff14.item.select",
                        {
                            "batch_id": "batch_id",
                            "generation": "generation",
                            "item_id": "item_id",
                            "action": "action",
                        },
                    ),
                    (
                        "ff14_logs_character",
                        "ff14.logs.character",
                        {
                            "realm": "realm",
                            "server": "server",
                            "character": "character",
                            "server_hint": "server_hint",
                            "metric": "metric",
                            "zone_id": "zone_id",
                            "difficulty": "difficulty",
                            "partition": "partition",
                            "action": "action",
                            "request_language": "request_language",
                            "context_language": "context_language",
                            "batch_id": "batch_id",
                            "generation": "generation",
                            "server_id": "server_id",
                        },
                    ),
                ),
            )
            self.assertEqual(
                tuple(
                    origin.value
                    for origin in capabilities["ff14.market.query"].invocation_origins
                ),
                ("command", "web_public", "llm_tool"),
            )
            self.assertEqual(
                tuple(
                    origin.value
                    for origin in capabilities["ff14.market.select"].invocation_origins
                ),
                ("command", "llm_tool"),
            )
            self.assertEqual(
                tuple(
                    origin.value
                    for origin in capabilities["item.lookup"].invocation_origins
                ),
                ("command", "web_public", "llm_tool"),
            )
            self.assertEqual(
                tuple(
                    origin.value
                    for origin in capabilities["ff14.item.select"].invocation_origins
                ),
                ("command", "llm_tool"),
            )
            self.assertEqual(len(module_manifest.schedules), 2)
            self.assertEqual(
                module_manifest.schedules[0].collector_id, "ff14.calendar.collect"
            )
            self.assertEqual(
                module_manifest.schedules[0].source_id, "ff14_calendar_primary"
            )
            self.assertEqual(module_manifest.schedules[0].default_interval_seconds, 900)
            sync = module_manifest.schedules[1]
            self.assertEqual(sync.collector_id, "ff14.calendar.sync")
            self.assertEqual(sync.trigger.value, "background")
            self.assertEqual(sync.default_interval_seconds, 21600)
            self.assertEqual(
                tuple(dict(v) for v in sync.background_parameters),
                ({"region": "cn"}, {"region": "global"}),
            )
            self.assertEqual(len(module_manifest.subscriptions), 1)
            self.assertEqual(
                module_manifest.subscriptions[0].notification_modes, ("instant",)
            )
            self.assertEqual(
                {field.name for field in module_manifest.config_fields},
                {
                    "credential_fflogs_cn",
                    "credential_fflogs_global",
                    "ff14_default_region",
                    "ff14_subscriptions_enabled",
                    "ff14_calendar_sync_interval_seconds",
                    "ff14_calendar_default_days",
                    "ff14_calendar_default_timezone",
                    "ff14_calendar_default_delivery_time",
                },
            )
            self.assertTrue(
                all(
                    not field.required
                    and field.sensitive == field.name.startswith("credential_")
                    for field in module_manifest.config_fields
                )
            )

            message_port = _MessagePort()
            transport = _FixtureTransport()

            async def source_health(module_id: str, source_id: str):
                if module_id == GLOBAL_MODULE_ID and source_id == "xivapi_items":
                    return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))
                return validate_contract(
                    CapabilityHealth(HealthStatus.UNAVAILABLE, "source_unavailable")
                )

            assembly = assemble_reviewed(
                CapturedAssemblyInventory(
                    package.package_id,
                    (installed_package / "yomihime.manifest.json").read_bytes(),
                    {
                        "assembly.json": (
                            installed_package / "assembly.json"
                        ).read_bytes()
                    },
                ),
                principal_id="ff14-test-config",
                support=SUPPORT,
                selected=True,
            )
            self.assertEqual(assembly.manifests[GLOBAL_MODULE_ID], module_manifest)
            policy = assembly.diagnostic_policies[GLOBAL_MODULE_ID]
            self.assertIn("CalendarSync", policy.event_ids)
            self.assertEqual(policy.event_limits["CalendarSync"], 48)
            self.assertEqual(
                dict(assembly.collector_timeouts),
                {(GLOBAL_MODULE_ID, "ff14.calendar.sync"): 45},
            )
            runtime = CoreRuntime(
                database=SQLiteDatabase(root / "core.sqlite3"),
                extension_root=extension_root,
                file_root=root / "files",
                secret_root=root / "secrets",
                secret_codec=None,
                http_transport=transport,
                renderer=_Renderer(),
                display_limits=validate_contract(DisplayLimits(16, 16_384)),
                message_port=message_port,
                admin_context_validator=lambda *_args: True,
                host_ingress_validator=lambda *_args: True,
                config_principal_id="ff14-test-config",
                identity_namespace="ff14-test-identities",
                trusted_bundled_manifests={GLOBAL_MODULE_ID: module_manifest},
                trusted_subscription_gates={
                    GLOBAL_MODULE_ID: TrustedSubscriptionGate(
                        module_manifest,
                        "ff14_subscriptions_enabled",
                        discovered[0]._provenance.manifest_sha256,
                    )
                },
                diagnostic_policies=assembly.diagnostic_policies,
                collector_timeouts=assembly.collector_timeouts,
                source_health=source_health,
                source_credential_policies=(
                    SourceCredentialPolicy(
                        GLOBAL_MODULE_ID,
                        "fflogs_public_cn",
                        "credential_fflogs_cn",
                        "cn.fflogs.com",
                        ("/api/v2/client",),
                        "cn.fflogs.com",
                        "/oauth/token",
                    ),
                    SourceCredentialPolicy(
                        GLOBAL_MODULE_ID,
                        "fflogs_public_global",
                        "credential_fflogs_global",
                        "www.fflogs.com",
                        ("/api/v2/client",),
                        "www.fflogs.com",
                        "/oauth/token",
                    ),
                ),
                pump_interval=3600,
                cleanup_timeout=0.5,
            )
            try:
                startup = await runtime.start()
                self.assertEqual(startup.extension_failures, ())
                self.assertEqual(len(runtime.extension_runtime.candidates()), 1)
                await runtime.admin_credential_repository.bootstrap(
                    _digest(token_urlsafe(32))
                )
                enabled = await runtime.admin_operations.set_enabled(
                    None,
                    GLOBAL_MODULE_ID,
                    True,
                    expected_registry_revision=runtime.registry.snapshot().revision,
                    authorization=_AdminContext(),
                )
                self.assertTrue(enabled.enabled)
                self.assertEqual(enabled.lifecycle.value, "active")
                # Real Core pump registration performs both public syncs without
                # any subscription, and subsequent scans do not fetch again.
                await runtime.scheduler.run_due_page()
                await asyncio.wait_for(transport.calendar_ready.wait(), timeout=10)
                await asyncio.gather(*tuple(runtime.scheduler._active.values()))
                completed_syncs = await runtime.database.executor.run_read(
                    lambda unit: tuple(
                        row[0]
                        for row in unit.execute(
                            "SELECT o.completeness FROM b04_background_jobs b "
                            "JOIN b04_collection_jobs j ON j.job_key=b.job_key "
                            "JOIN b04_observations o ON o.observation_id=j.observation_id"
                        )
                    )
                )
                self.assertEqual(completed_syncs, ("complete", "complete"))
                calendar_requests = [
                    r
                    for r in transport.requests
                    if r.source_id == "ff14_calendar_primary"
                ]
                self.assertEqual(len(calendar_requests), 2)
                self.assertEqual(len({r.path for r in calendar_requests}), 2)
                self.assertEqual(
                    await runtime.database.executor.run_read(
                        lambda unit: unit.execute(
                            "SELECT COUNT(*) FROM b04_subscriptions"
                        ).fetchone()[0]
                    ),
                    0,
                )
                await runtime.scheduler.run_due_page()
                self.assertEqual(len(transport.requests), 2)

                self.assertNotIn(
                    "ff14.logs.output_percentile",
                    runtime.registry.snapshot()
                    .module_for_route("ff14")
                    .handlers.capabilities,
                )
                bridge = AstrBotCommandBridge(runtime.registry)
                self.assertIn(
                    "未找到命令",
                    bridge.parse(_TextEvent("/ygl ff14 output 国服 副本 难度 职业")),
                )
                parsed_calendar = bridge.parse(
                    _TextEvent(
                        "/ygl ff14 calendar 国际服 days=7 timezone=Asia/Shanghai"
                    )
                )
                self.assertIsInstance(parsed_calendar, CommandInvocation)
                self.assertEqual(
                    dict(parsed_calendar.parameters),
                    {
                        "region": "国际服",
                        "days": 7,
                        "timezone": "Asia/Shanghai",
                    },
                )
                parsed_list = bridge.parse(
                    _TextEvent("/ygl ff14 calendar subscriptions page=2")
                )
                self.assertIsInstance(parsed_list, CommandInvocation)
                self.assertEqual(dict(parsed_list.parameters), {"page": 2})

                ingress = HostIngress(
                    adapter_id="test-adapter",
                    actor_id="alice",
                    conversation_id="alice-direct",
                    delivery_route="alice-direct-route",
                    conversation_kind=ConversationKind.DIRECT,
                    evidence=object(),
                )
                status = await runtime.invoke_command(
                    GLOBAL_MODULE_ID, "status", {}, ingress=ingress
                )
                self.assertEqual(status.result.status.value, "success")
                self.assertEqual(status.result.privacy.value, "public")
                self.assertIn("FFLogs", message_port.calls[-1][1].text)
                self.assertNotIn("HTTP 403", message_port.calls[-1][1].text)
                self.assertIn(
                    "未执行实时健康或凭据检查", message_port.calls[-1][1].text
                )

                item = await runtime.invoke_command(
                    GLOBAL_MODULE_ID, "item", {"query": "90001"}, ingress=ingress
                )
                self.assertEqual(item.result.status.value, "partial_success")
                self.assertEqual(item.result.privacy.value, "public")
                self.assertIn("Sample Ore Alpha", message_port.calls[-1][1].text)
                self.assertTrue(
                    all(
                        isinstance(request, TransportRequest)
                        for request in transport.requests
                    )
                )
                self.assertEqual(
                    [
                        (request.source_id, request.path)
                        for request in transport.requests
                        if not request.source_id.startswith("ff14_calendar_")
                    ],
                    [
                        ("xivapi_items", "/api/sheet/Item/90001"),
                        ("garland_items", "/db/doc/item/chs/3/90001.json"),
                    ],
                )

                before_fflogs = len(transport.requests)
                logs_handler = (
                    runtime.registry.snapshot()
                    .module(GLOBAL_MODULE_ID)
                    .handlers.capabilities["ff14.logs.character"]
                )
                with patch.object(
                    logs_handler, "invoke", wraps=logs_handler.invoke
                ) as invoked:
                    malformed = await runtime.invoke_command(
                        GLOBAL_MODULE_ID,
                        "logs",
                        {
                            "realm": "cn",
                            "server": "Test Server",
                            "character": "Test Character",
                        },
                        ingress=ingress,
                    )
                    self.assertEqual(malformed.result.status.value, "error")
                    self.assertEqual(
                        malformed.result.error.code.value, "parameter_error"
                    )
                    invoked.assert_not_called()
                    self.assertEqual(len(transport.requests), before_fflogs)
                    self.assertEqual(transport.credential_exchanges, [])

                    parsed_logs = bridge.parse(
                        _TextEvent('/ygl ff14 logs "Synthetic World" "Synthetic Hero"')
                    )
                    self.assertIsInstance(parsed_logs, CommandInvocation)
                    self.assertEqual(
                        dict(parsed_logs.parameters),
                        {"command": '"Synthetic World" "Synthetic Hero"'},
                    )
                    missing_credentials = await runtime.invoke_command(
                        parsed_logs.module_id,
                        parsed_logs.operation_path,
                        parsed_logs.parameters,
                        ingress=ingress,
                    )
                    invoked.assert_awaited_once()
                self.assertEqual(missing_credentials.result.status.value, "error")
                self.assertEqual(
                    missing_credentials.result.error.code.value, "auth_required"
                )
                self.assertCountEqual(
                    [
                        (request.source_id, request.path)
                        for request in transport.requests[before_fflogs:]
                    ],
                    [
                        ("universalis_market", "/api/v2/worlds"),
                        ("universalis_market", "/api/v2/data-centers"),
                    ],
                )
                self.assertEqual(
                    [
                        request
                        for request in transport.requests
                        if request.source_id.startswith("fflogs_")
                    ],
                    [],
                )
                self.assertEqual(transport.credential_exchanges, [])

                before_calendar = len(transport.requests)
                calendar = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar",
                    {"region": "\u56fd\u670d"},
                    ingress=ingress,
                )
                self.assertEqual(calendar.result.status.value, "success")
                self.assertEqual(calendar.result.privacy.value, "public")
                self.assertEqual(len(transport.requests), before_calendar)
                manual = bridge.parse(
                    _TextEvent("/ygl ff14 calendar 国服 refresh=true")
                )
                self.assertIsInstance(manual, CommandInvocation)
                self.assertIs(manual.parameters["refresh"], True)
                calendar_handler = (
                    runtime.registry.snapshot()
                    .module(GLOBAL_MODULE_ID)
                    .handlers.capabilities["ff14.calendar.query"]
                )
                snapshot_errors = []
                original_read = calendar_handler._snapshots.read

                async def checked_read(*args, **kwargs):
                    try:
                        return await original_read(*args, **kwargs)
                    except Exception as exc:
                        snapshot_errors.append(f"{type(exc).__name__}: {exc}")
                        raise

                with patch.object(
                    calendar_handler._snapshots, "read", side_effect=checked_read
                ):
                    refreshed = await runtime.invoke_command(
                        manual.module_id,
                        manual.operation_path,
                        manual.parameters,
                        ingress=ingress,
                    )
                self.assertEqual(snapshot_errors, [])
                self.assertEqual(
                    refreshed.result.status.value, "success", repr(refreshed.result)
                )
                self.assertEqual(len(transport.requests), before_calendar)

                created = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar subscribe",
                    {"region": "cn"},
                    ingress=ingress,
                )
                self.assertEqual(created.result.status.value, "success")
                self.assertEqual(created.result.privacy.value, "private")
                created_text = " ".join(
                    block.text for block in created.result.document.ordered_blocks
                )
                target = re.search(
                    r"([A-Za-z0-9_-]{20,30}).*?revision\s+(\d+)",
                    created_text,
                    re.IGNORECASE,
                )
                self.assertIsNotNone(target, created_text)
                subscription_id, revision_text = target.groups()
                self.assertEqual(revision_text, "1")

                listed = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar subscriptions",
                    {},
                    ingress=ingress,
                )
                self.assertEqual(listed.result.status.value, "success")
                self.assertEqual(listed.result.privacy.value, "private")
                listed_text = " ".join(
                    block.text for block in listed.result.document.ordered_blocks
                )
                self.assertIn(subscription_id, listed_text)

                direct_send_count = len(message_port.calls)
                group_ingress = HostIngress(
                    adapter_id="test-adapter",
                    actor_id="alice",
                    conversation_id="shared-group",
                    delivery_route="shared-group-route",
                    conversation_kind=ConversationKind.GROUP,
                    evidence=object(),
                )
                group_create = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar subscribe",
                    {"region": "global"},
                    ingress=group_ingress,
                )
                self.assertIsNone(group_create.result)
                self.assertEqual(len(message_port.calls), direct_send_count)

                listed_again = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar subscriptions",
                    {},
                    ingress=ingress,
                )
                listed_again_text = " ".join(
                    block.text for block in listed_again.result.document.ordered_blocks
                )
                self.assertIn(subscription_id, listed_again_text)
                self.assertNotIn("global", listed_again_text)

                updated = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar update",
                    {
                        "subscription_id": subscription_id,
                        "expected_revision": 1,
                        "time": "09:00",
                    },
                    ingress=ingress,
                )
                self.assertEqual(updated.result.status.value, "success")
                updated_text = " ".join(
                    block.text for block in updated.result.document.ordered_blocks
                )
                self.assertIn("revision 2", updated_text)

                cancelled = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar cancel",
                    {"subscription_id": subscription_id, "expected_revision": 2},
                    ingress=ingress,
                )
                self.assertEqual(cancelled.result.status.value, "success")
                self.assertEqual(cancelled.result.privacy.value, "private")
            finally:
                self.assertTrue(await runtime.close(timeout=1))
            self.assertTrue(runtime.closed)


if __name__ == "__main__":
    unittest.main()
