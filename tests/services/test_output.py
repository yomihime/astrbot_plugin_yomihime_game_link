"""B04-O tests use deterministic result ports and an offline registry."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import (
    CommandOutput,
    Grant,
    GrantStatus,
    Principal,
    SubscriptionOutput,
    ToolOutput,
)
from ygl_test_subject.core.contracts.subscriptions import (
    DeliveryEvent,
    DeliveryState,
    SubscriptionRecord,
    SubscriptionStatus,
    delivery_idempotency_key,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import (
    MessageReceipt,
    MessageStatus,
    RootOutputOutcome,
)
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_identity import (
    SQLiteIdentityRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_output import (
    SQLiteRootOutputRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteSubscriptionStore,
)
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.output import (
    LifecycleApprovedSendScheduler,
    OutputService,
    OutputStatus,
)
from ygl_test_subject.services.owner_authority import OwnerRouteProofAuthority

from tests.fixtures.b04_runtime import _run_async_from_sync
from tests.fixtures.minimal_module import build_package
from yomihime_game_link_sdk.contexts import (
    InvocationConversationKind,
    InvocationOrigin,
    InvocationSubscriptionScope,
)
from yomihime_game_link_sdk.declarations import (
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
    DisplayOutput,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from yomihime_game_link_sdk.storage import OwnerScope
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    ConversationKind,
    ConversationRef,
    NormalizedInput,
)


class _OutputModuleInstance:
    def __init__(self, handlers: ModuleHandlers) -> None:
        self._handlers = handlers

    def handlers(self) -> ModuleHandlers:
        return self._handlers

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        return validate_contract(
            HealthReport(
                {
                    "lookup": validate_contract(
                        CapabilityHealth(HealthStatus.AVAILABLE)
                    ),
                    "status": validate_contract(
                        CapabilityHealth(HealthStatus.AVAILABLE)
                    ),
                }
            )
        )


class _Renderer:
    def __init__(self, *, fail: bool = False):
        self.calls = []
        self.fail = fail

    async def render(self, document, *, limits, audience):
        self.calls.append((document, limits, audience))
        if self.fail:
            raise ValueError("decoder and path details must not escape")
        return validate_contract(DisplayOutput("rendered result", ("img:one",)))

    async def render_batch(self, batch, limits):  # pragma: no cover - protocol only
        raise AssertionError("root result routing does not batch render")


class _Conversations:
    def __init__(self, route=None):
        self.route = route
        self.calls = []

    async def resolve(self, invocation):
        self.calls.append(invocation)
        return self.route


class _MessagePort:
    def __init__(self, receipt=None, *, error=None, started=None, release=None):
        self.calls = []
        self.receipt = receipt or MessageReceipt(MessageStatus.ACCEPTED, "platform-1")
        self.error = error
        self.started = started
        self.release = release

    async def send(self, target, payload):
        self.calls.append((target, payload))
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.receipt


class _Grants:
    def __init__(self, grant=None):
        self.grant = grant
        self.calls = []

    async def current_grant(self, grant_id):
        self.calls.append(grant_id)
        return self.grant


class _MutablePrincipalResolver:
    def __init__(self, principal_id: str):
        self.value = principal_id

    async def principal_id(self, invocation):
        return self.value


class _ResourceVisibility:
    def __init__(self, database):
        self.database = database
        self.fail = False
        self.calls = []

    async def contains_non_public_resource_reference(self, text):
        self.calls.append(text)
        if self.fail:
            raise OSError("visibility lookup unavailable")
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM assets WHERE scope_kind!='public' "
                "AND instr(?, asset_id)>0 LIMIT 1",
                (text,),
            ).fetchone()
        finally:
            connection.close()
        return row is not None


class _GatedRootOutputs:
    def __init__(self, repository):
        self.repository = repository
        self.claimed = asyncio.Event()
        self.release = asyncio.Event()

    async def claim(self, *args, **kwargs):
        claim = await self.repository.claim(*args, **kwargs)
        self.claim_snapshot = claim
        self.claimed.set()
        await self.release.wait()
        return claim

    def __getattr__(self, name):
        return getattr(self.repository, name)


class _GatedCompleteRootOutputs:
    def __init__(self, repository):
        self.repository = repository
        self.claim_snapshot = None
        self.completing = asyncio.Event()
        self.release_complete = asyncio.Event()

    async def claim(self, *args, **kwargs):
        self.claim_snapshot = await self.repository.claim(*args, **kwargs)
        return self.claim_snapshot

    async def complete(self, *args, **kwargs):
        self.completing.set()
        await self.release_complete.wait()
        return await self.repository.complete(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.repository, name)


class _GatedBeginRootOutputs:
    """Pause after the real SQLite SENDING transaction commits."""

    def __init__(self, repository):
        self.repository = repository
        self.committed = asyncio.Event()
        self.release = asyncio.Event()
        self.claim_snapshot = None
        self.sending_snapshot = None

    async def claim(self, *args, **kwargs):
        self.claim_snapshot = await self.repository.claim(*args, **kwargs)
        return self.claim_snapshot

    async def begin_sending(self, *args, **kwargs):
        self.sending_snapshot = await self.repository.begin_sending(*args, **kwargs)
        self.committed.set()
        await self.release.wait()
        return self.sending_snapshot

    def __getattr__(self, name):
        return getattr(self.repository, name)


class _CancelBeforeRunScheduler(LifecycleApprovedSendScheduler):
    """Exercise cancellation after scope registration but before the runner starts."""

    def schedule(self, permit, task, *, name):
        super().schedule(permit, task, name=name)
        owned = self._lifecycle.scope(permit.module_id).tasks[-1]
        owned.cancel()


class OutputServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "output.sqlite3"
        self.db = SQLiteDatabase(self.db_path)
        self.db.initialize()
        self.root_outputs = SQLiteRootOutputRepository(self.db)
        self.deliveries = SQLiteDeliveryRepository(self.db)
        self.subscriptions = SQLiteSubscriptionStore(self.db)
        self.registry = Registry()
        package, handlers = build_package()
        self.registry.register_package(package, {"demo": handlers})
        self.issuer = ContextIssuer()
        self.lifecycle = LifecycleController(self.registry, issuer=self.issuer)
        self._install_and_activate(self.registry, package, handlers, self.lifecycle)
        self.snapshot = self.registry.snapshot()
        self.module = self.snapshot.modules["sample/demo"]
        self.admission = self.lifecycle.admission
        self.identities = SQLiteIdentityRepository(self.db)
        await self.identities.save_principal(
            Principal("principal-offline", "output-users", "offline-actor")
        )
        self.principal_resolver = InvocationPrincipalResolver(
            self.issuer,
            self.identities,
            identity_namespace="output-users",
            admission=self.admission,
        )
        self.send_scheduler = LifecycleApprovedSendScheduler(self.lifecycle)
        self.route = validate_contract(
            ConversationRef(
                "offline-adapter",
                ConversationKind.DIRECT,
                "offline-conversation",
                "direct:actor",
            )
        )
        self.renderer = _Renderer()
        self.message_port = _MessagePort()
        self.conversations = _Conversations(self.route)
        self.grants = _Grants()
        self.resource_visibility = _ResourceVisibility(self.db)
        self.output = self._service()

    def _service(self, **changes):
        dependencies = {
            "issuer": self.issuer,
            "admission": self.admission,
            "send_scheduler": self.send_scheduler,
            "registry": self.registry,
            "renderer": self.renderer,
            "limits": validate_contract(
                DisplayLimits(max_pages=2, max_image_bytes=1024)
            ),
            "conversations": self.conversations,
            "message_port": self.message_port,
            "deliveries": self.deliveries,
            "grants": self.grants,
            "subscriptions": self.subscriptions,
            "root_outputs": self.root_outputs,
            "resource_visibility": self.resource_visibility,
            "principal_resolver": self.principal_resolver,
            "claim_lease": timedelta(minutes=1),
            "send_timeout": 5.0,
            "now": lambda: datetime(2026, 9, 25, tzinfo=UTC),
        }
        dependencies.update(changes)
        return OutputService(**dependencies)

    async def _owner_output(self, *, root_outputs=None):
        package, handlers = build_package()
        original = package.modules[0]
        owner_lookup = replace(
            original.capabilities[0],
            invocation_policy=InvocationPolicy.COMMAND_ONLY,
            privacy_floor=PrivacyFloor.OWNER,
        )
        owner_module = validate_contract(
            ModuleManifest(
                original.module_id,
                original.route,
                ModuleCategory.GAME,
                original.factory_entry,
                original.module_version,
                (owner_lookup, original.capabilities[1]),
                commands=(
                    validate_contract(
                        CommandDescriptor("read", "lookup", {"item": "item"}, "Read")
                    ),
                ),
            )
        )
        owner_package = validate_contract(
            PackageManifest(
                package.package_id,
                package.package_version,
                package.contract_version,
                (owner_module,),
                package.author,
                package.license,
                package.source,
            )
        )
        registry = Registry()
        registry.register_package(owner_package, {"demo": handlers})
        issuer = ContextIssuer()
        lifecycle = LifecycleController(registry, issuer=issuer)
        self._install_and_activate(registry, owner_package, handlers, lifecycle)
        snapshot = registry.snapshot()
        module = snapshot.modules["sample/demo"]
        route = self.route
        conversations = _Conversations(route)
        principals = _MutablePrincipalResolver("principal-offline")
        authority = OwnerRouteProofAuthority(
            issuer, lifecycle.admission, principals, conversations
        )
        view = issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="sample/demo",
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id="offline-actor",
            adapter_id=route.adapter_id,
            conversation_id=route.conversation_id,
            capability_id="lookup",
        )
        lease = lifecycle.admission.admit(view, "lookup")
        await authority.capture(view, lease)
        service = self._service(
            registry=registry,
            issuer=issuer,
            admission=lifecycle.admission,
            send_scheduler=LifecycleApprovedSendScheduler(lifecycle),
            conversations=conversations,
            principal_resolver=principals,
            owner_authority=authority,
            root_outputs=root_outputs or self.root_outputs,
        )
        return service, view, principals, conversations, authority

    async def _assert_controlled_claim(self, gate, code):
        claim = gate.claim_snapshot
        persisted = await self.root_outputs.claim(
            claim.root_invocation_id,
            claim.output_identity,
            claim.payload_fingerprint,
            claim.owner_token,
            now=datetime(2026, 9, 25, tzinfo=UTC),
            lease_expires_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=1),
        )
        self.assertEqual(persisted.outcome, RootOutputOutcome.CONTROLLED_RESULT)
        self.assertEqual(persisted.error_code, code)

    def _view(self, origin=InvocationOrigin.COMMAND, **changes):
        fields = {
            "origin": origin,
            "module_id": "sample/demo",
            "module_epoch": self.module.epoch,
            "registry_revision": self.snapshot.revision,
            "actor_id": "offline-actor",
            "conversation_id": self.route.conversation_id,
            "adapter_id": self.route.adapter_id,
            "capability_id": "lookup",
        }
        fields.update(changes)
        view = self.issuer.issue(**fields)
        if fields["origin"] in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL):
            try:
                self.admission.admit(view, fields["capability_id"])
            except Exception:
                pass
        return view

    @staticmethod
    def _install_and_activate(registry, package, handlers, lifecycle) -> None:
        module = registry.snapshot().module(f"{package.package_id}/demo")
        instance = _OutputModuleInstance(handlers)
        install_operation = "output-test-install"
        captured = lifecycle.adopt_candidate(
            package.package_id, module.manifest, install_operation, instance
        )
        lifecycle.install_dormant(
            package.package_id,
            module.module_id,
            install_operation,
            instance,
            captured,
        )
        start_operation = "output-test-start"
        identity, _ = _run_async_from_sync(
            lifecycle.start_candidate(module.module_id, start_operation)
        )
        lifecycle.publish_committed_intent(
            module.module_id,
            start_operation,
            identity,
            True,
            registry.snapshot().revision,
        )

    async def _disable(self) -> None:
        operation = f"output-test-stop-{self.lifecycle.current_identity('sample/demo').module_epoch}"
        identity = self.lifecycle.quiesce("sample/demo", operation, "test_disable")
        self.lifecycle.publish_committed_intent(
            "sample/demo", operation, identity, False, self.registry.snapshot().revision
        )
        await self.lifecycle.stop_candidate("sample/demo", operation, 1.0)

    async def _enable(self) -> None:
        operation = f"output-test-restart-{self.registry.snapshot().module('sample/demo').epoch}"
        identity, _ = await self.lifecycle.start_candidate("sample/demo", operation)
        self.lifecycle.publish_committed_intent(
            "sample/demo", operation, identity, True, self.registry.snapshot().revision
        )
        self.snapshot = self.registry.snapshot()
        self.module = self.snapshot.modules["sample/demo"]

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _result(*, privacy=Privacy.PUBLIC, facts=True):
        document = validate_contract(
            DisplayDocument(
                "lookup",
                "offline item",
                (validate_contract(TextBlock("visible result")),),
                privacy=privacy,
            )
        )
        return validate_contract(
            CapabilityResult(
                "offline-result",
                ResultStatus.SUCCESS,
                document=document,
                model_facts=(
                    validate_contract(FactDocument({"value": "public"}))
                    if facts
                    else None
                ),
                privacy=privacy,
            )
        )

    async def test_auth_errors_use_fixed_chinese_recovery_without_raw_details(self):
        for code, expected in (
            (ErrorCode.AUTH_REQUIRED, "此来源需要授权。"),
            (ErrorCode.AUTH_EXPIRED, "此来源授权已失效。"),
        ):
            result = validate_contract(
                CapabilityResult(
                    "auth-error",
                    ResultStatus.ERROR,
                    error=validate_contract(
                        ErrorDetail(code, "private upstream payload must never render")
                    ),
                )
            )
            rendered = await self.output._render(result)
            self.assertTrue(rendered.text.startswith(expected))
            self.assertIn("管理员", rendered.text)
            self.assertIn("对应来源", rendered.text)
            self.assertIn("请勿在聊天中发送凭据", rendered.text)
            self.assertNotIn("private", rendered.text)
            self.assertNotIn("FFLogs", rendered.text)

    async def test_o01_command_sends_once_and_preserves_accepted_receipt(self):
        view = self._view()
        request = CommandOutput(self._result())
        first = await self.output.route(view, request)
        reopened = SQLiteRootOutputRepository(SQLiteDatabase(self.db_path))
        second = await self._service(root_outputs=reopened).route(view, request)

        self.assertEqual(first.status, OutputStatus.SENT)
        self.assertEqual(first.receipt, self.message_port.receipt)
        self.assertEqual(second, first)
        self.assertEqual(len(self.message_port.calls), 1)
        target, payload = self.message_port.calls[0]
        self.assertEqual(target.conversation, self.route)
        self.assertEqual(target.recipient_id, "offline-actor")
        self.assertEqual(payload.text, "rendered result")
        self.assertEqual(payload.resource_ids, ("img:one",))

    async def test_o02_nested_capability_returns_structured_result_without_send(self):
        parent = self._view()
        child = self.issuer.derive(
            parent,
            module_id="sample/demo",
            module_epoch=self.module.epoch,
            capability_id="lookup",
        )
        request = CommandOutput(self._result())

        output = await self.output.route(child, request)

        self.assertEqual(output.status, OutputStatus.NESTED_RESULT)
        self.assertIs(output.result, request)
        self.assertEqual(self.message_port.calls, [])
        self.assertEqual(self.renderer.calls, [])

    async def test_o03_tool_projects_only_public_facts_without_side_send(self):
        view = self._view(InvocationOrigin.LLM_TOOL)

        output = await self.output.route(view, self._result())

        self.assertEqual(output.status, OutputStatus.TOOL_RESULT)
        self.assertIsInstance(output.result, ToolOutput)
        self.assertEqual(dict(output.result.facts.facts), {"value": "public"})
        self.assertEqual(self.message_port.calls, [])
        self.assertEqual(self.renderer.calls, [])

    async def test_o04_tool_rejects_private_result_and_granted_tool(self):
        view = self._view(InvocationOrigin.LLM_TOOL)
        private = self._result(privacy=Privacy.PRIVATE, facts=False)
        denied = await self.output.route(view, private)
        self.assertEqual(denied.error_code, "tool_result_not_public")

        granted_view = self._view(
            InvocationOrigin.LLM_TOOL, grant_id="grant-1", grant_revision=1
        )
        granted = await self.output.route(granted_view, self._result())
        self.assertEqual(granted.error_code, "tool_result_not_public")
        self.assertEqual(self.message_port.calls, [])
        self.assertEqual(self.renderer.calls, [])

    async def test_o05_standalone_tool_output_cannot_skip_source_privacy_check(self):
        view = self._view(InvocationOrigin.LLM_TOOL)
        output = await self.output.route(
            view, ToolOutput(validate_contract(FactDocument({"value": "public"})))
        )
        self.assertEqual(output.error_code, "tool_provenance_required")

    async def test_o05_private_resource_reference_and_probe_failure_are_not_projected(
        self,
    ):
        private_id = "private-asset-42"
        connection = self.db.connect()
        try:
            connection.execute(
                "INSERT INTO assets(asset_id,media_type,scope_kind,user_id,grant_id,"
                "grant_revision,size_bytes,expires_at,temporary,revision) "
                "VALUES(?, ?, 'user', 'offline-actor', NULL, NULL, 1, NULL, 1, 1)",
                (private_id, "application/octet-stream"),
            )
        finally:
            connection.close()
        source = replace(
            self._result(),
            model_facts=validate_contract(
                FactDocument(
                    {"nested": [{"resource_ref": f"see {private_id} in this text"}]},
                    sources=(f"source mentions {private_id}",),
                )
            ),
        )
        view = self._view(InvocationOrigin.LLM_TOOL)
        rejected = await self.output.route(view, source)
        self.assertEqual(rejected.error_code, "tool_result_not_public")
        self.assertNotIn(private_id, repr(rejected))
        self.assertTrue(
            any(private_id in candidate for candidate in self.resource_visibility.calls)
        )

        public_source = self._result()
        self.resource_visibility.fail = True
        unavailable = await self._service().route(
            self._view(InvocationOrigin.LLM_TOOL), public_source
        )
        self.assertEqual(unavailable.error_code, "tool_resource_visibility_unavailable")
        self.assertIsNone(unavailable.result)

    async def test_subscription_enqueue_uses_exact_issued_scope_and_replays(self):
        record = SubscriptionRecord(
            "subscription-one",
            1,
            "sample/demo",
            validate_contract(
                CollectionKey(
                    "sample/demo",
                    "collector",
                    1,
                    "source",
                    validate_contract(NormalizedInput({"x": 1})),
                    OwnerScope.public(),
                )
            ),
            "principal-offline",
            None,
            self.route,
            "instant",
            {},
            SubscriptionStatus.ACTIVE,
        )
        await self.subscriptions.create(record)
        view = self.issuer.issue(
            origin=InvocationOrigin.SUBSCRIPTION,
            module_id="sample/demo",
            module_epoch=self.module.epoch,
            registry_revision=self.snapshot.revision,
            actor_id="principal-offline",
            subscription_id="subscription-one",
            subscription_revision=1,
            adapter_id=self.route.adapter_id,
            conversation_id=self.route.conversation_id,
            delivery_route=self.route.delivery_route,
            conversation_kind=InvocationConversationKind.DIRECT,
            subscription_scope=InvocationSubscriptionScope.PUBLIC,
            capability_id="lookup",
        )
        self.admission.admit(view, "lookup")
        document = self._result().document
        event = DeliveryEvent(
            event_key="event-one",
            event_version=1,
            subscription_id="subscription-one",
            subscription_revision=1,
            owner_id="principal-offline",
            grant=None,
            recipient=self.route,
            display_data=document,
            idempotency_key=delivery_idempotency_key(
                "event-one", 1, "subscription-one", 1, self.route
            ),
            state=DeliveryState.PENDING,
        )
        request = SubscriptionOutput(event)
        output = await self.output.route(view, request)
        self.assertEqual(
            output.status, OutputStatus.SUBSCRIPTION_ENQUEUED, output.error_code
        )
        self.assertEqual(
            await self.deliveries.current_event(
                "event-one",
                1,
                subscription_id="subscription-one",
                subscription_revision=1,
            ),
            event,
        )
        replay = await self._service().route(view, request)
        self.assertEqual(replay.status, OutputStatus.SUBSCRIPTION_ENQUEUED)
        self.assertEqual(self.message_port.calls, [])

    async def test_o06_progress_is_non_output_and_final_result_sends_once(self):
        view = self._view()
        await self.output.progress(view)
        await self.output.progress(view)
        result = await self.output.route(view, CommandOutput(self._result()))

        self.assertEqual(result.status, OutputStatus.SENT)
        self.assertEqual(len(self.message_port.calls), 1)

    async def test_o07_forged_or_stale_invocation_fails_closed(self):
        view = self._view()
        forged = replace(view, origin=InvocationOrigin.LLM_TOOL)
        output = await self.output.route(forged, CommandOutput(self._result()))
        self.assertEqual(output.error_code, "invocation_unavailable")
        self.assertEqual(self.message_port.calls, [])

        stale = self._view()
        await self._disable()
        rejected = await self.output.route(stale, CommandOutput(self._result()))
        self.assertEqual(rejected.error_code, "invocation_unavailable")
        self.assertEqual(self.message_port.calls, [])

    async def test_o08_same_invocation_different_output_is_conflict(self):
        view = self._view()
        await self.output.route(view, CommandOutput(self._result()))
        conflict = await self.output.route(
            view,
            CommandOutput(
                validate_contract(
                    CapabilityResult(
                        "different",
                        ResultStatus.SUCCESS,
                        document=validate_contract(
                            DisplayDocument(
                                "other",
                                "subject",
                                (validate_contract(TextBlock("other")),),
                            )
                        ),
                    )
                )
            ),
        )
        self.assertEqual(conflict.error_code, "root_output_conflict")
        self.assertEqual(len(self.message_port.calls), 1)

    async def test_invocation_lock_is_shared_until_last_waiter_then_reclaimed(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class GatedRenderer(_Renderer):
            async def render(inner, document, *, limits, audience):
                if not started.is_set():
                    started.set()
                    await release.wait()
                return await super().render(document, limits=limits, audience=audience)

        service = self._service(renderer=GatedRenderer())
        view = self._view()
        request = CommandOutput(self._result())
        first = asyncio.create_task(service.route(view, request))
        await started.wait()
        entry = service._locks[view.invocation_id]

        second = asyncio.create_task(service.route(view, request))
        await asyncio.sleep(0)
        self.assertIs(service._locks[view.invocation_id], entry)
        self.assertEqual(entry.users, 2)

        release.set()
        first_result = await first
        self.assertEqual(first_result.status, OutputStatus.SENT)
        self.assertIs(service._locks[view.invocation_id], entry)
        self.assertEqual(entry.users, 1)

        second_result = await second
        self.assertEqual(second_result.status, OutputStatus.SENT)
        self.assertNotIn(view.invocation_id, service._locks)
        self.assertEqual(len(self.message_port.calls), 1)

        for _ in range(24):
            await service.route(self._view(), CommandOutput(self._result()))
        self.assertEqual(service._locks, {})

    async def test_o09_render_failure_and_host_receipts_are_not_misreported(self):
        view = self._view()
        self.renderer.fail = True
        failed_render = await self.output.route(view, CommandOutput(self._result()))
        self.assertEqual(failed_render.status, OutputStatus.FAILED)
        self.assertEqual(failed_render.error_code, "display_render_failed")
        self.assertEqual(self.message_port.calls, [])

        self.renderer.fail = False
        failed_port = _MessagePort(MessageReceipt(MessageStatus.FAILED))
        service = self._service(message_port=failed_port)
        second_view = self._view()
        result = await service.route(second_view, CommandOutput(self._result()))
        self.assertEqual(result.status, OutputStatus.FAILED)
        self.assertEqual(result.receipt, failed_port.receipt)

        unknown_port = _MessagePort(MessageReceipt(MessageStatus.UNKNOWN))
        unknown_service = self._service(message_port=unknown_port)
        third_view = self._view()
        unknown = await unknown_service.route(third_view, CommandOutput(self._result()))
        self.assertEqual(unknown.status, OutputStatus.UNKNOWN)
        self.assertEqual(unknown.receipt, unknown_port.receipt)

    async def test_malformed_renderer_output_never_reaches_message_port(self):
        for malformed in (
            DisplayOutput("", ()),
            DisplayOutput("text", ("../private",)),
            DisplayOutput("text", ("asset", "asset")),
        ):

            class BadRenderer(_Renderer):
                async def render(self, *args, **kwargs):
                    return malformed

            service = self._service(renderer=BadRenderer())
            result = await service.route(self._view(), CommandOutput(self._result()))
            self.assertEqual(result.status, OutputStatus.FAILED)
            self.assertEqual(result.error_code, "display_render_failed")
            self.assertEqual(self.message_port.calls, [])

    async def test_o09_timeout_and_send_exception_require_recovery_without_resend(self):
        view = self._view()
        started = asyncio.Event()
        release = asyncio.Event()
        port = _MessagePort(started=started, release=release)
        service = self._service(message_port=port, send_timeout=0.01)
        timed_out = await service.route(view, CommandOutput(self._result()))
        await started.wait()

        self.assertEqual(timed_out.status, OutputStatus.UNKNOWN)
        self.assertIsNone(timed_out.receipt)
        self.assertEqual(len(port.calls), 1)
        recovered = await self.root_outputs.recover_expired(
            before=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=2),
            recovered_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=2),
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state.value, "unknown")
        release.set()
        await asyncio.sleep(0)
        replay = await self._service(message_port=port).route(
            view, CommandOutput(self._result())
        )
        self.assertEqual(replay.status, OutputStatus.UNKNOWN)
        self.assertEqual(len(port.calls), 1)

        error_view = self._view()
        error_port = _MessagePort(error=OSError("host transport disconnected"))
        error_service = self._service(message_port=error_port)
        uncertain = await error_service.route(error_view, CommandOutput(self._result()))
        self.assertEqual(uncertain.status, OutputStatus.UNKNOWN)
        self.assertIsNone(uncertain.receipt)
        self.assertEqual(len(error_port.calls), 1)
        pending = await self._service(message_port=error_port).route(
            error_view, CommandOutput(self._result())
        )
        self.assertEqual(pending.status, OutputStatus.UNKNOWN)
        self.assertEqual(len(error_port.calls), 1)
        recovered_error = await self.root_outputs.recover_expired(
            before=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=2),
            recovered_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=2),
        )
        self.assertEqual(recovered_error, ())
        after_recovery = await self._service(message_port=error_port).route(
            error_view, CommandOutput(self._result())
        )
        self.assertEqual(after_recovery.status, OutputStatus.UNKNOWN)
        self.assertEqual(len(error_port.calls), 1)

    async def test_deadline_expiring_after_sqlite_sending_cas_aborts_before_port(self):
        gate = _GatedBeginRootOutputs(self.root_outputs)
        monotonic_now = [100.0]
        self.issuer._clock = lambda: monotonic_now[0]
        service = self._service(root_outputs=gate)
        view = self._view(deadline=101.0)

        sending = asyncio.create_task(
            service.route(view, CommandOutput(self._result()))
        )
        await gate.committed.wait()
        monotonic_now[0] = 102.0
        gate.release.set()
        result = await sending

        self.assertEqual(result.status, OutputStatus.FAILED)
        self.assertEqual(result.error_code, "invocation_unavailable")
        self.assertEqual(self.message_port.calls, [])
        persisted = await self.root_outputs.claim(
            gate.claim_snapshot.root_invocation_id,
            gate.claim_snapshot.output_identity,
            gate.claim_snapshot.payload_fingerprint,
            gate.claim_snapshot.owner_token,
            now=datetime(2026, 9, 25, tzinfo=UTC),
            lease_expires_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=1),
        )
        self.assertEqual(persisted.state.value, "completed")
        self.assertEqual(persisted.error_code, "invocation_unavailable")

    async def test_owner_private_output_sends_only_to_exact_direct_route(self):
        service, view, _, _, _ = await self._owner_output()

        result = await service.route(
            view, CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False))
        )

        self.assertEqual(result.status, OutputStatus.SENT)
        self.assertEqual(len(self.message_port.calls), 1)
        target, _ = self.message_port.calls[0]
        self.assertEqual(target.conversation, self.route)
        self.assertEqual(target.recipient_id, "offline-actor")

    async def test_owner_principal_or_route_drift_before_claim_never_sends(self):
        principal_service, principal_view, principals, _, _ = await self._owner_output()
        principals.value = "principal-b"
        principal_result = await principal_service.route(
            principal_view,
            CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False)),
        )
        self.assertEqual(principal_result.status, OutputStatus.FAILED)
        self.assertEqual(self.message_port.calls, [])

        route_service, route_view, _, conversations, _ = await self._owner_output()
        conversations.route = replace(self.route, delivery_route="direct:changed")
        route_result = await route_service.route(
            route_view,
            CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False)),
        )
        self.assertEqual(route_result.status, OutputStatus.FAILED)
        self.assertEqual(self.message_port.calls, [])

    async def test_owner_principal_change_after_sending_cas_aborts_before_host_send(
        self,
    ):
        gate = _GatedBeginRootOutputs(self.root_outputs)
        service, view, principals, _, _ = await self._owner_output(root_outputs=gate)
        sending = asyncio.create_task(
            service.route(
                view, CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False))
            )
        )
        await gate.committed.wait()
        principals.value = "principal-b"
        gate.release.set()

        result = await sending

        self.assertEqual(result.status, OutputStatus.FAILED)
        self.assertEqual(result.error_code, "invocation_unavailable")
        self.assertEqual(self.message_port.calls, [])

    async def test_owner_route_change_after_sending_cas_aborts_before_host_send(self):
        gate = _GatedBeginRootOutputs(self.root_outputs)
        service, view, _, conversations, _ = await self._owner_output(root_outputs=gate)
        sending = asyncio.create_task(
            service.route(
                view, CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False))
            )
        )
        await gate.committed.wait()
        conversations.route = replace(self.route, delivery_route="direct:changed")
        gate.release.set()

        result = await sending

        self.assertEqual(result.status, OutputStatus.FAILED)
        self.assertEqual(result.error_code, "invocation_unavailable")
        self.assertEqual(self.message_port.calls, [])

    async def test_root_send_claim_expiring_after_sending_cas_aborts(self):
        gate = _GatedBeginRootOutputs(self.root_outputs)
        wall_now = [datetime(2026, 9, 25, tzinfo=UTC)]
        service = self._service(
            root_outputs=gate,
            now=lambda: wall_now[0],
            claim_lease=timedelta(seconds=1),
        )
        view = self._view()

        sending = asyncio.create_task(
            service.route(view, CommandOutput(self._result()))
        )
        await gate.committed.wait()
        wall_now[0] = gate.sending_snapshot.lease_expires_at
        gate.release.set()
        result = await sending

        self.assertEqual(result.error_code, "invocation_unavailable")
        self.assertEqual(self.message_port.calls, [])
        persisted = await self.root_outputs.claim(
            gate.claim_snapshot.root_invocation_id,
            gate.claim_snapshot.output_identity,
            gate.claim_snapshot.payload_fingerprint,
            gate.claim_snapshot.owner_token,
            now=datetime(2026, 9, 25, tzinfo=UTC),
            lease_expires_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=1),
        )
        self.assertEqual(persisted.state.value, "completed")
        self.assertEqual(persisted.error_code, "invocation_unavailable")

    async def test_grant_expiring_after_root_sending_cas_aborts_before_port(self):
        gate = _GatedBeginRootOutputs(self.root_outputs)
        wall_now = [datetime(2026, 9, 25, tzinfo=UTC)]
        expires_at = wall_now[0] + timedelta(seconds=2)
        self.grants.grant = Grant(
            "grant-root-expiry",
            1,
            "principal-offline",
            "sample/demo",
            "account-root-expiry",
            ("lookup",),
            None,
            GrantStatus.ACTIVE,
            expires_at,
        )
        service = self._service(root_outputs=gate, now=lambda: wall_now[0])
        view = self._view(grant_id="grant-root-expiry", grant_revision=1)

        sending = asyncio.create_task(
            service.route(
                view,
                CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False)),
            )
        )
        await gate.committed.wait()
        wall_now[0] = expires_at
        gate.release.set()
        result = await sending

        self.assertEqual(result.error_code, "grant_unavailable")
        self.assertEqual(self.message_port.calls, [])
        persisted = await self.root_outputs.claim(
            gate.claim_snapshot.root_invocation_id,
            gate.claim_snapshot.output_identity,
            gate.claim_snapshot.payload_fingerprint,
            gate.claim_snapshot.owner_token,
            now=datetime(2026, 9, 25, tzinfo=UTC),
            lease_expires_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=1),
        )
        self.assertEqual(persisted.state.value, "completed")
        self.assertEqual(persisted.error_code, "grant_unavailable")

    async def test_private_root_grant_uses_internal_principal_and_external_recipient(
        self,
    ):
        self.grants.grant = Grant(
            "grant-principal-map",
            1,
            "principal-offline",
            "sample/demo",
            "account-principal-map",
            ("lookup",),
            None,
            GrantStatus.ACTIVE,
        )
        view = self._view(grant_id="grant-principal-map", grant_revision=1)
        result = await self.output.route(
            view,
            CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False)),
        )
        self.assertEqual(result.status, OutputStatus.SENT)
        self.assertEqual(self.message_port.calls[-1][0].recipient_id, "offline-actor")

    async def test_cancelled_scope_task_before_first_run_recovers_unknown_without_resend(
        self,
    ):
        scheduler = _CancelBeforeRunScheduler(self.lifecycle)
        port = _MessagePort()
        service = self._service(
            send_scheduler=scheduler, message_port=port, send_timeout=0.01
        )
        view = self._view()

        result = await service.route(view, CommandOutput(self._result()))

        self.assertEqual(result.status, OutputStatus.UNKNOWN)
        self.assertEqual(port.calls, [])
        await asyncio.sleep(0)
        self.assertEqual(self.lifecycle.scope("sample/demo").tasks, ())
        recovered = await self.root_outputs.recover_expired(
            before=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=2),
            recovered_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=2),
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state.value, "unknown")
        replay = await service.route(view, CommandOutput(self._result()))
        self.assertEqual(replay.status, OutputStatus.UNKNOWN)
        self.assertEqual(port.calls, [])

    async def test_cancellation_during_root_sending_commit_drains_exact_abort(self):
        gate = _GatedBeginRootOutputs(self.root_outputs)
        service = self._service(root_outputs=gate)
        view = self._view()
        task = asyncio.create_task(service.route(view, CommandOutput(self._result())))

        await gate.committed.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(self.message_port.calls, [])
        persisted = await self.root_outputs.claim(
            gate.claim_snapshot.root_invocation_id,
            gate.claim_snapshot.output_identity,
            gate.claim_snapshot.payload_fingerprint,
            gate.claim_snapshot.owner_token,
            now=datetime(2026, 9, 25, tzinfo=UTC),
            lease_expires_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=1),
        )
        self.assertEqual(persisted.state.value, "completed")
        self.assertEqual(persisted.error_code, "cancelled_before_dispatch")

    async def test_o10_other_registered_module_uses_same_generic_renderer(self):
        second_registry = Registry()
        second_package, handlers = build_package()
        second_package = replace(second_package, package_id="sample_two")
        second_registry.register_package(second_package, {"demo": handlers})
        second_issuer = ContextIssuer()
        second_lifecycle = LifecycleController(second_registry, issuer=second_issuer)
        self._install_and_activate(
            second_registry, second_package, handlers, second_lifecycle
        )
        second_admission = second_lifecycle.admission
        second_scheduler = LifecycleApprovedSendScheduler(second_lifecycle)
        enabled = second_registry.snapshot()
        second_module = enabled.modules["sample_two/demo"]
        view = second_issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="sample_two/demo",
            module_epoch=second_module.epoch,
            registry_revision=enabled.revision,
            actor_id="another-actor",
            conversation_id="another-conversation",
            adapter_id="offline-adapter",
            capability_id="lookup",
        )
        second_admission.admit(view, "lookup")
        port = _MessagePort()
        service = self._service(
            issuer=second_issuer,
            admission=second_admission,
            send_scheduler=second_scheduler,
            registry=second_registry,
            renderer=_Renderer(),
            conversations=_Conversations(
                validate_contract(
                    ConversationRef(
                        "offline-adapter",
                        ConversationKind.GROUP,
                        "another-conversation",
                        "group:1",
                    )
                )
            ),
            message_port=port,
        )

        result = await service.route(view, CommandOutput(self._result()))
        self.assertEqual(result.status, OutputStatus.SENT)
        self.assertIsNone(port.calls[0][0].recipient_id)

    async def test_claim_gate_rechecks_command_and_tool_module_authority(self):
        for origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL):
            with self.subTest(origin=origin):
                view = self._view(origin)
                gate = _GatedRootOutputs(self.root_outputs)
                service = self._service(root_outputs=gate)
                task = asyncio.create_task(service.route(view, self._result()))
                await gate.claimed.wait()
                await self._disable()
                gate.release.set()
                result = await task
                self.assertEqual(result.error_code, "invocation_unavailable")
                self.assertEqual(self.message_port.calls, [])
                self.assertIsNone(result.result)
                await self._assert_controlled_claim(gate, "invocation_unavailable")
                # Restore for the second parameterized subtest with a fresh epoch.
                await self._enable()

    async def test_claim_gate_rechecks_command_grant_and_route(self):
        active_grant = Grant(
            "grant-one",
            1,
            "principal-offline",
            "sample/demo",
            "account-one",
            ("lookup",),
            None,
            GrantStatus.ACTIVE,
        )
        self.grants.grant = active_grant
        grant_view = self._view(grant_id="grant-one", grant_revision=1)
        gate = _GatedRootOutputs(self.root_outputs)
        service = self._service(root_outputs=gate)
        grant_task = asyncio.create_task(
            service.route(
                grant_view,
                CommandOutput(self._result(privacy=Privacy.PRIVATE, facts=False)),
            )
        )
        await gate.claimed.wait()
        self.grants.grant = replace(
            active_grant, status=GrantStatus.REVOKED, revision=2
        )
        gate.release.set()
        grant_result = await grant_task
        self.assertEqual(grant_result.error_code, "grant_unavailable")
        self.assertEqual(self.message_port.calls, [])
        await self._assert_controlled_claim(gate, "grant_unavailable")

        route_view = self._view()
        route_gate = _GatedRootOutputs(self.root_outputs)
        route_service = self._service(root_outputs=route_gate)
        route_task = asyncio.create_task(
            route_service.route(route_view, CommandOutput(self._result()))
        )
        await route_gate.claimed.wait()
        self.conversations.route = replace(self.route, delivery_route="direct:changed")
        route_gate.release.set()
        route_result = await route_task
        self.assertEqual(route_result.error_code, "route_changed")
        self.assertEqual(self.message_port.calls, [])
        await self._assert_controlled_claim(route_gate, "route_changed")

    async def test_tool_completion_gate_rechecks_module_before_exposing_facts(self):
        view = self._view(InvocationOrigin.LLM_TOOL)
        gate = _GatedCompleteRootOutputs(self.root_outputs)
        service = self._service(root_outputs=gate)
        task = asyncio.create_task(service.route(view, self._result()))
        await gate.completing.wait()
        await self._disable()
        gate.release_complete.set()
        result = await task

        self.assertEqual(result.error_code, "invocation_unavailable")
        self.assertIsNone(result.result)
        self.assertEqual(self.message_port.calls, [])
        claim = gate.claim_snapshot
        persisted = await self.root_outputs.claim(
            claim.root_invocation_id,
            claim.output_identity,
            claim.payload_fingerprint,
            claim.owner_token,
            now=datetime(2026, 9, 25, tzinfo=UTC),
            lease_expires_at=datetime(2026, 9, 25, tzinfo=UTC) + timedelta(minutes=1),
        )
        self.assertEqual(persisted.outcome, RootOutputOutcome.TOOL_RETURNED)


if __name__ == "__main__":
    unittest.main()
