from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from secrets import token_urlsafe

from ygl_test_subject.adapters.astrbot.command_bridge import (
    AstrBotCommandBridge,
    CommandInvocation,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import MessageReceipt, MessageStatus
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.infrastructure.http import TransportRequest
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.admin_authorization import _digest
from ygl_test_subject.services.core_runtime import (
    CoreRuntime,
    HostIngress,
    TrustedSubscriptionGate,
)
from ygl_test_subject.services.source_credentials import SourceCredentialPolicy

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

    async def request(self, request: TransportRequest) -> HttpResponse:
        self.requests.append(request)
        if request.source_id == "xivapi_items" and request.path.endswith("/search"):
            payload = self.fixture["xivcdn"]["search_first_page"]
        elif request.source_id == "xivapi_items" and request.path.endswith("/90001"):
            payload = self.fixture["xivcdn"]["item_detail"]
        elif request.source_id == "garland_items" and request.path.endswith(
            "/90001.json"
        ):
            payload = self.fixture["garland"]["linked_item_detail"]
        elif request.source_id == "ff14_calendar_primary":
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
        del request
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
                    "status",
                    "item",
                    "logs",
                    "output",
                    "calendar",
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
            self.assertEqual(
                capabilities["ff14.logs.output_percentile"].required_sources, ()
            )
            self.assertEqual(
                capabilities["ff14.logs.output_percentile"].required_config, ()
            )
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
                        },
                    ),
                    (
                        "ff14_market_select",
                        "ff14.market.select",
                        {
                            "batch_id": "batch_id",
                            "generation": "generation",
                            "item_id": "item_id",
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
            self.assertEqual(len(module_manifest.schedules), 1)
            self.assertEqual(
                module_manifest.schedules[0].collector_id, "ff14.calendar.collect"
            )
            self.assertEqual(
                module_manifest.schedules[0].source_id, "ff14_calendar_primary"
            )
            self.assertEqual(module_manifest.schedules[0].default_interval_seconds, 900)
            self.assertEqual(len(module_manifest.subscriptions), 1)
            self.assertEqual(
                module_manifest.subscriptions[0].notification_modes, ("instant",)
            )
            self.assertEqual(
                {field.name for field in module_manifest.config_fields},
                {
                    "credential_fflogs_cn",
                    "credential_fflogs_global",
                    "ff14_subscriptions_enabled",
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

                output_health, _ = runtime.health_resolver.current(
                    GLOBAL_MODULE_ID, "ff14.logs.output_percentile"
                )
                self.assertIs(output_health.status, HealthStatus.UNAVAILABLE)
                self.assertIn("HTTP 403", output_health.reason)

                bridge = AstrBotCommandBridge(runtime.registry)
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
                self.assertIn("HTTP 403", message_port.calls[-1][1].text)

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
                    ],
                    [
                        ("xivapi_items", "/api/sheet/Item/90001"),
                        ("garland_items", "/db/doc/item/chs/3/90001.json"),
                    ],
                )

                before_fflogs = len(transport.requests)
                missing_credentials = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "logs",
                    {
                        "realm": "cn",
                        "server": "Test Server",
                        "character": "Test Character",
                    },
                    ingress=ingress,
                )
                self.assertEqual(missing_credentials.result.status.value, "error")
                self.assertEqual(
                    missing_credentials.result.error.code.value, "auth_required"
                )
                self.assertEqual(len(transport.requests), before_fflogs)

                calendar = await runtime.invoke_command(
                    GLOBAL_MODULE_ID,
                    "calendar",
                    {"region": "\u56fd\u670d"},
                    ingress=ingress,
                )
                self.assertEqual(calendar.result.status.value, "success")
                self.assertEqual(calendar.result.privacy.value, "public")
                self.assertEqual(
                    transport.requests[-1].source_id, "ff14_calendar_primary"
                )

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
