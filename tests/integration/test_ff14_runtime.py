from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from zoneinfo import ZoneInfo

from ygl_test_subject.api.administration import AdminAuthorizationDenied
from ygl_test_subject.api.display import DisplayLimits
from ygl_test_subject.api.services import CapabilityHealth, HealthStatus, HttpResponse
from ygl_test_subject.api.subscriptions import ConversationKind, DeliveryState
from ygl_test_subject.core.ports import MessageStatus
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

from tests.fixtures.b04_runtime import DeterministicClock, RecordingMessagePort
from tests.modules.ff14.test_integration import (
    GLOBAL_MODULE_ID,
    _AdminContext,
    _Renderer,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE_SOURCE = ROOT / "modules" / "ff14"


class _CalendarTransport:
    """Offline HTTP boundary; Core, collector, parser, and scheduler stay real."""

    def __init__(self, event_local_date) -> None:
        self.primary_unavailable = False
        self.fallback_unavailable = False
        self.requests: list[TransportRequest] = []
        event_day = event_local_date.strftime("%Y%m%d")
        self.calendar_ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Yomihime Tests//FF14 Calendar//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:ff14-runtime-due-event\r\n"
            f"DTSTAMP:{event_day}T000000Z\r\n"
            f"DTSTART:{event_day}T020000Z\r\n"
            f"DTEND:{event_day}T030000Z\r\n"
            "SUMMARY:Integration Raid Event\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ).encode("ascii")

    async def request(self, request: TransportRequest) -> HttpResponse:
        self.requests.append(request)
        if request.source_id == "ff14_calendar_primary":
            if self.primary_unavailable:
                return HttpResponse(503, {"content-type": "text/plain"}, b"offline")
            return HttpResponse(
                200, {"content-type": "text/calendar"}, self.calendar_ics
            )
        if request.source_id == "ff14_calendar_fallback":
            if self.fallback_unavailable:
                return HttpResponse(503, {"content-type": "text/plain"}, b"offline")
            return HttpResponse(
                200, {"content-type": "text/calendar"}, self.calendar_ics
            )
        return HttpResponse(404, {}, b"")

    async def request_credential_exchange(self, request) -> HttpResponse:
        del request
        raise AssertionError(
            "calendar integration must not exchange FFLogs credentials"
        )


class FF14CalendarRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self._temporary.name)
        self.extension_root = self.root / "extensions"
        self.extension_root.mkdir()
        shutil.copytree(MODULE_SOURCE, self.extension_root / "ff14")
        self.clock = DeterministicClock(datetime(2026, 1, 14, 23, 59, tzinfo=UTC))
        self.message_port = RecordingMessagePort()
        self.event_date = self.clock().astimezone(ZoneInfo("Asia/Shanghai")).date()
        self.transport = _CalendarTransport(self.event_date)
        self._evidence = {actor: object() for actor in ("alice", "bob", "mallory")}
        self._admin_context = _AdminContext()
        self._runtimes: list[CoreRuntime] = []
        self.runtime = await self._open_runtime()

    async def asyncTearDown(self) -> None:
        for runtime in reversed(self._runtimes):
            if not runtime.closed:
                self.assertTrue(await runtime.close(timeout=1))
        self._temporary.cleanup()

    async def _open_runtime(self, *, trusted_gate: bool = True) -> CoreRuntime:
        def validate_ingress(_origin, ingress: HostIngress) -> bool:
            return ingress.evidence is self._evidence.get(ingress.actor_id)

        def validate_admin(_operation, _invocation, context, _generation) -> bool:
            return context is self._admin_context

        packages = discover_packages(self.extension_root)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertTrue(package.valid)
        self.assertTrue(package._provenance.trusted)
        self.assertEqual(package.package_id, "ff14")
        self.assertEqual(len(package.manifest.modules), 1)
        manifest = package.manifest.modules[0]
        runtime = CoreRuntime(
            database=SQLiteDatabase(self.root / "core.sqlite3"),
            extension_root=self.extension_root,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=None,
            http_transport=self.transport,
            renderer=_Renderer(),
            display_limits=DisplayLimits(16, 16_384),
            message_port=self.message_port,
            admin_context_validator=validate_admin,
            host_ingress_validator=validate_ingress,
            config_principal_id="ff14-runtime-config",
            identity_namespace="ff14-runtime-identities",
            trusted_bundled_manifests={GLOBAL_MODULE_ID: manifest},
            trusted_subscription_gates={
                GLOBAL_MODULE_ID: TrustedSubscriptionGate(
                    manifest,
                    "ff14_subscriptions_enabled",
                    package._provenance.manifest_sha256,
                )
            }
            if trusted_gate
            else None,
            source_health=self._source_health,
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
            utc_clock=self.clock,
            pump_interval=3600,
            cleanup_timeout=0.5,
        )
        self._runtimes.append(runtime)
        startup = await runtime.start()
        self.assertEqual(startup.extension_failures, ())
        self.assertEqual(len(runtime.extension_runtime.candidates()), 1)
        try:
            enabled_now = runtime.registry.snapshot().module(GLOBAL_MODULE_ID).enabled
        except Exception:
            enabled_now = False
        if not enabled_now:
            credential_state = await runtime.admin_credential_repository.current()
            if credential_state.status.value == "UNINITIALIZED":
                await runtime.admin_credential_repository.bootstrap(
                    _digest(token_urlsafe(32))
                )
            enabled = await runtime.admin_operations.set_enabled(
                None,
                GLOBAL_MODULE_ID,
                True,
                expected_registry_revision=runtime.registry.snapshot().revision,
                authorization=self._admin_context,
            )
            self.assertEqual(enabled.lifecycle.value, "active")
        # Inject only the deterministic clock seam into the real loaded collector.
        collector = runtime.lifecycle.handlers(GLOBAL_MODULE_ID).collectors[
            "ff14.calendar.collect"
        ]
        collector._clock = self.clock
        return runtime

    @staticmethod
    async def _source_health(_module_id: str, _source_id: str):
        return CapabilityHealth(HealthStatus.AVAILABLE)

    def _ingress(
        self,
        actor: str,
        *,
        kind: ConversationKind = ConversationKind.DIRECT,
        evidence: object | None = None,
    ) -> HostIngress:
        token = self._evidence[actor] if evidence is None else evidence
        suffix = "direct" if kind is ConversationKind.DIRECT else "group"
        conversation_id = f"{actor}-{suffix}"
        return HostIngress(
            adapter_id="test-adapter",
            actor_id=actor,
            conversation_id=conversation_id,
            delivery_route=f"{conversation_id}-route",
            conversation_kind=kind,
            evidence=token,
        )

    async def _command(
        self,
        actor: str,
        operation: str,
        parameters: dict[str, object],
        *,
        kind: ConversationKind = ConversationKind.DIRECT,
        evidence: object | None = None,
        runtime: CoreRuntime | None = None,
    ):
        current = self.runtime if runtime is None else runtime
        return await current.invoke_command(
            GLOBAL_MODULE_ID,
            operation,
            parameters,
            ingress=self._ingress(actor, kind=kind, evidence=evidence),
        )

    @staticmethod
    def _document_text(outcome) -> str:
        if outcome.result is None or outcome.result.document is None:
            return ""
        return "\n".join(
            block.text
            for block in outcome.result.document.ordered_blocks
            if hasattr(block, "text")
        )

    @classmethod
    def _subscription_id(cls, outcome) -> str:
        match = re.search(r"订阅 ([^\s（]+)（revision 1）", cls._document_text(outcome))
        if match is None:
            raise AssertionError(f"subscription receipt missing ID: {outcome!r}")
        return match.group(1)

    async def _subscribe(
        self,
        actor: str,
        *,
        timezone: str = "Asia/Shanghai",
        local_time: str = "08:00",
    ) -> str:
        outcome = await self._command(
            actor,
            "calendar subscribe",
            {"region": "国服", "timezone": timezone, "time": local_time},
        )
        self.assertEqual(
            outcome.result.status.value, "success", repr(outcome.result.error)
        )
        self.assertEqual(outcome.result.privacy.value, "private")
        return self._subscription_id(outcome)

    async def test_missing_trusted_gate_keeps_subscriptions_unavailable(self) -> None:
        self.assertTrue(await self.runtime.close(timeout=1))
        self.runtime = await self._open_runtime(trusted_gate=False)
        outcome = await self._command("alice", "calendar subscribe", {"region": "cn"})
        self.assertEqual(outcome.result.status.value, "error")
        self.assertEqual(outcome.result.error.code.value, "upstream_error")
        self.assertEqual(
            await self.runtime.b04_repositories.scheduler.list_due_jobs(
                now=self.clock(), limit=10
            ),
            (),
        )
        # The command's private error receipt is delivered normally; no
        # subscription delivery or collection job may be created.
        self.assertEqual(len(self.message_port.calls), 1)
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 0)
        self.assertEqual(len(self.message_port.calls), 1)

    async def test_missing_and_forged_admin_evidence_cannot_disable_module(
        self,
    ) -> None:
        before = self.runtime.registry.snapshot()
        for context in (None, _AdminContext()):
            with (
                self.subTest(context=context),
                self.assertRaises(AdminAuthorizationDenied),
            ):
                await self.runtime.admin_operations.set_enabled(
                    None,
                    GLOBAL_MODULE_ID,
                    False,
                    expected_registry_revision=before.revision,
                    authorization=context,
                )
        self.assertEqual(self.runtime.registry.snapshot(), before)
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 0)
        self.assertEqual(len(self.message_port.calls), 0)

    async def _run_collection(self, runtime: CoreRuntime | None = None) -> int:
        current = self.runtime if runtime is None else runtime
        return (await current.scheduler.run_due_page()).processed

    async def test_trusted_direct_subscribers_share_one_public_region_job(self) -> None:
        before_messages = len(self.message_port.calls)
        self.assertEqual(await self._run_collection(), 0)
        self.assertEqual(len(self.message_port.calls), before_messages)
        self.assertEqual(
            await self.runtime.b04_repositories.scheduler.list_due_jobs(
                now=self.clock(), limit=10
            ),
            (),
        )

        group_attempt = await self._command(
            "alice",
            "calendar subscribe",
            {"region": "cn"},
            kind=ConversationKind.GROUP,
        )
        self.assertIsNone(group_attempt.result)
        with self.assertRaises(PermissionError):
            await self._command(
                "mallory",
                "calendar subscribe",
                {"region": "cn"},
                evidence=object(),
            )
        self.assertEqual(
            await self.runtime.b04_repositories.scheduler.list_due_jobs(
                now=self.clock(), limit=10
            ),
            (),
        )

        alice_id = await self._subscribe("alice")
        bob_id = await self._subscribe(
            "bob", timezone="Asia/Shanghai", local_time="08:05"
        )
        alice = await self.runtime.b04_repositories.subscriptions.current(alice_id)
        bob = await self.runtime.b04_repositories.subscriptions.current(bob_id)
        self.assertIsNotNone(alice)
        self.assertIsNotNone(bob)
        self.assertEqual(alice.collection_key, bob.collection_key)
        self.assertEqual(alice.collection_key.scope.kind.value, "public")
        self.assertEqual(dict(alice.collection_key.parameters.values), {"region": "cn"})
        self.assertEqual(alice.filters["time"], "08:00")
        self.assertEqual(bob.filters["time"], "08:05")
        jobs = await self.runtime.b04_repositories.scheduler.list_due_jobs(
            now=self.clock(), limit=10
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].key, alice.collection_key)
        self.assertEqual(jobs[0].cadence_seconds, 900)

        foreign = await self._command(
            "bob",
            "calendar update",
            {
                "subscription_id": alice_id,
                "expected_revision": 1,
                "time": "10:00",
            },
        )
        foreign_text = self._document_text(foreign)
        self.assertEqual(foreign.result.status.value, "error")
        self.assertNotIn("08:00", foreign_text)
        alice_after = await self.runtime.b04_repositories.subscriptions.current(
            alice_id
        )
        self.assertEqual(alice_after.revision, 1)

    async def test_baseline_due_delivery_source_switch_and_unknown_survive_restart(
        self,
    ) -> None:
        alice_id = await self._subscribe("alice")
        bob_id = await self._subscribe("bob", local_time="08:05")
        alice = await self.runtime.b04_repositories.subscriptions.current(alice_id)
        bob = await self.runtime.b04_repositories.subscriptions.current(bob_id)
        self.assertEqual(alice.collection_key, bob.collection_key)
        message_count = len(self.message_port.calls)

        self.assertEqual(await self._run_collection(), 1)
        snapshot = await self.runtime.b04_repositories.scheduler.current_evaluation(
            alice_id, alice.collection_key
        )
        self.assertIsNotNone(snapshot.state)
        self.assertEqual(
            snapshot.state.value["baseline_local_date"], self.event_date.isoformat()
        )
        self.assertEqual(len(self.message_port.calls), message_count)
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 0)
        self.assertEqual(len(self.message_port.calls), message_count)

        self.clock.advance(timedelta(minutes=20))
        self.assertEqual(await self._run_collection(), 1)
        self.message_port.outcomes.extend(
            (MessageStatus.UNKNOWN, MessageStatus.ACCEPTED)
        )
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 2)
        self.assertEqual(len(self.message_port.calls), message_count + 2)
        event_key = f"ff14.calendar.daily.{self.event_date.isoformat()}"
        alice_event = await self.runtime.b04_repositories.deliveries.current_event(
            event_key,
            1,
            subscription_id=alice_id,
            subscription_revision=1,
        )
        bob_event = await self.runtime.b04_repositories.deliveries.current_event(
            event_key,
            1,
            subscription_id=bob_id,
            subscription_revision=1,
        )
        self.assertEqual(
            {alice_event.state, bob_event.state},
            {DeliveryState.UNKNOWN, DeliveryState.SENT},
        )
        unknown_id = alice_id if alice_event.state is DeliveryState.UNKNOWN else bob_id
        last_complete_observation_id = (
            await self.runtime.b04_repositories.scheduler.current_evaluation(
                alice_id, alice.collection_key
            )
        ).observation.observation_id

        self.transport.primary_unavailable = True
        self.transport.fallback_unavailable = True
        self.clock.advance(timedelta(minutes=20))
        self.assertEqual(await self._run_collection(), 1)
        failed_snapshot = (
            await self.runtime.b04_repositories.scheduler.current_evaluation(
                alice_id, alice.collection_key
            )
        )
        self.assertEqual(
            failed_snapshot.observation.observation_id,
            last_complete_observation_id,
        )
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 0)
        self.assertEqual(len(self.message_port.calls), message_count + 2)

        self.transport.fallback_unavailable = False
        self.clock.advance(timedelta(minutes=20))
        self.assertEqual(await self._run_collection(), 1)
        self.assertIn(
            "ff14_calendar_fallback",
            [request.source_id for request in self.transport.requests],
        )
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 0)
        self.assertEqual(len(self.message_port.calls), message_count + 2)

        old_runtime = self.runtime
        self.assertTrue(await old_runtime.close(timeout=1))
        self.runtime = await self._open_runtime()
        self.clock.advance(timedelta(minutes=20))
        self.assertEqual(await self._run_collection(), 1)
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 0)
        self.assertEqual(len(self.message_port.calls), message_count + 2)
        recovered_unknown = (
            await self.runtime.b04_repositories.deliveries.current_event(
                event_key,
                1,
                subscription_id=unknown_id,
                subscription_revision=1,
            )
        )
        self.assertEqual(recovered_unknown.state, DeliveryState.UNKNOWN)

    async def test_revision_cancel_cas_and_delivery_history(self) -> None:
        subscription_id = await self._subscribe("alice")
        record = await self.runtime.b04_repositories.subscriptions.current(
            subscription_id
        )
        self.assertEqual(await self._run_collection(), 1)
        self.clock.advance(timedelta(minutes=20))
        self.assertEqual(await self._run_collection(), 1)
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 1)

        stale = await self._command(
            "alice",
            "calendar update",
            {
                "subscription_id": subscription_id,
                "expected_revision": 7,
                "time": "09:00",
            },
        )
        self.assertEqual(stale.result.status.value, "error")
        current = await self.runtime.b04_repositories.subscriptions.current(
            subscription_id
        )
        self.assertEqual(current.revision, 1)

        updated = await self._command(
            "alice",
            "calendar update",
            {
                "subscription_id": subscription_id,
                "expected_revision": 1,
                "time": "09:00",
            },
        )
        self.assertEqual(updated.result.status.value, "success")
        revised = await self.runtime.b04_repositories.subscriptions.current(
            subscription_id
        )
        self.assertEqual(revised.revision, 2)

        # Revision 2 gets a fresh baseline, while the already delivered local
        # date stays consumed across revision boundaries.
        for _ in range(3):
            self.clock.advance(timedelta(minutes=20))
            self.assertEqual(await self._run_collection(), 1)
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(), 0)
        revised_day_event = (
            await self.runtime.b04_repositories.deliveries.current_event(
                f"ff14.calendar.daily.{self.event_date.isoformat()}",
                1,
                subscription_id=subscription_id,
                subscription_revision=2,
            )
        )
        self.assertIsNone(revised_day_event)

        cancelled = await self._command(
            "alice",
            "calendar cancel",
            {"subscription_id": subscription_id, "expected_revision": 2},
        )
        self.assertEqual(cancelled.result.status.value, "success")
        ended = await self.runtime.b04_repositories.subscriptions.current(
            subscription_id
        )
        self.assertEqual(ended.revision, 3)
        self.assertEqual(ended.status.value, "cancelled")

        self.clock.advance(timedelta(minutes=20))
        self.assertEqual(await self._run_collection(), 0)
        history = await self.runtime.b04_repositories.deliveries.current_event(
            f"ff14.calendar.daily.{self.event_date.isoformat()}",
            1,
            subscription_id=subscription_id,
            subscription_revision=1,
        )
        self.assertEqual(history.state, DeliveryState.SENT)
        self.assertEqual(record.revision, 1)


if __name__ == "__main__":
    unittest.main()
