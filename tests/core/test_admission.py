from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from ygl_test_subject.api.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from ygl_test_subject.api.storage import OwnerScope, OwnershipKind
from ygl_test_subject.api.subscriptions import (
    CollectionKey,
    NormalizedInput,
    ScheduleDescriptor,
    ScheduleTrigger,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.admission import (
    AdmissionError,
    CapabilityUnavailable,
    MutationReentryError,
)
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import ExecutionLease, SendApproval


class _Handler:
    async def invoke(self, context, parameters):
        return None


class _Collector:
    def normalize(self, parameters):
        return None

    async def collect(self, context, parameters, previous):
        return None


class _Instance:
    def __init__(self, handlers):
        self._handlers = handlers

    def handlers(self):
        return self._handlers

    async def start(self):
        return None

    async def stop(self):
        return None

    async def check_health(self):
        return HealthReport({"read": CapabilityHealth(HealthStatus.AVAILABLE)})


def _registry() -> tuple[object, ModuleHandlers]:
    schedule = ScheduleDescriptor(
        collector_id="prices",
        key_version=1,
        source_id="steam",
        data_version=1,
        input_schema={"type": "object", "properties": {}, "required": []},
        shared_scope=OwnershipKind.PUBLIC,
        trigger=ScheduleTrigger.ON_DEMAND,
        minimum_interval_seconds=30,
    )
    manifest = ModuleManifest(
        module_id="mod",
        route="mod",
        category=ModuleCategory.GAME,
        factory_entry="tests:Factory",
        module_version="1.0.0",
        capabilities=(
            CapabilityDescriptor(
                capability_id="read",
                input_schema={"type": "object"},
                invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
                effect=CapabilityEffect.READ_ONLY,
            ),
        ),
        commands=(
            CommandDescriptor(
                operation_path="read",
                capability_id="read",
                parameter_mapping={},
                help_text="read",
            ),
        ),
        schedules=(schedule,),
    )
    package = PackageManifest(
        package_id="pkg",
        package_version="1.0.0",
        contract_version=CONTRACT_VERSION,
        modules=(manifest,),
        author="tests",
        license="AGPL-3.0",
        source="offline",
    )
    handlers = ModuleHandlers(
        {"read": _Handler()},
        {"prices": _Collector()},
        {},
    )
    from ygl_test_subject.core.registry import Registry

    registry = Registry()
    registry.register_package(package, {"mod": handlers})
    return registry, handlers


async def _activate(*, claim_prover=None, health_query=None):
    registry, handlers = _registry()
    controller = LifecycleController(
        registry,
        execution_claim_prover=claim_prover,
        capability_health_query=health_query,
    )
    install_id = uuid4().hex
    instance = _Instance(handlers)
    handlers = controller.adopt_candidate(
        "pkg", registry.snapshot().module("pkg/mod").manifest, install_id, instance
    )
    controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)
    operation_id = uuid4().hex
    identity, _ = await controller.start_candidate("pkg/mod", operation_id)
    controller.publish_committed_intent(
        "pkg/mod", operation_id, identity, True, registry.snapshot().revision
    )
    return registry, controller, identity


def _view(controller, identity):
    return controller.issuer.issue(
        origin=InvocationOrigin.COMMAND,
        module_id=identity.module_id,
        module_epoch=identity.module_epoch,
        registry_revision=controller.registry.snapshot().revision,
        actor_id="user",
        conversation_id="conversation",
        capability_id="read",
    )


class _Scheduler:
    def __init__(self):
        self.tasks = []
        self.permits = []

    def schedule(self, permit, task, *, name):
        self.permits.append((permit, name))
        self.tasks.append(asyncio.create_task(task, name=name))


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_is_exact_health_versioned_and_released_with_view(self):
        current = [CapabilityHealth(HealthStatus.AVAILABLE), 4]

        def health_query(module_id, capability_id, base, revision):
            return tuple(current)

        _, controller, identity = await _activate(health_query=health_query)
        view = _view(controller, identity)
        lease = controller.admission.admit(view, "read")
        self.assertEqual(lease.health_revision, 4)
        controller.admission.check(lease)
        with self.assertRaises(AdmissionError):
            controller.admission.check(replace(lease))

        current[:] = [CapabilityHealth(HealthStatus.UNAVAILABLE), 5]
        with self.assertRaises(CapabilityUnavailable):
            controller.admission.check(lease)

        controller.issuer.release(view)
        with self.assertRaises(AdmissionError):
            controller.admission.check(lease)
        controller.admission.release(lease)
        controller.admission.release(lease)
        self.assertNotIn(lease.lease_id, controller.admission._leases)
        self.assertNotIn(view.invocation_id, controller.admission._view_leases)

    async def test_duplicate_admission_preserves_original_lease_until_release(self):
        _, controller, identity = await _activate()
        view = _view(controller, identity)
        lease = controller.admission.admit(view, "read")

        with self.assertRaises(AdmissionError):
            controller.admission.admit(view, "read")

        self.assertIs(controller.issuer.lease_for(view), lease)
        self.assertIs(controller.admission._leases[lease.lease_id].lease, lease)
        self.assertEqual(
            controller.admission._view_leases[view.invocation_id], lease.lease_id
        )
        controller.issuer.release(view)
        controller.admission.release(lease)
        self.assertNotIn(lease.lease_id, controller.admission._leases)
        self.assertNotIn(view.invocation_id, controller.admission._view_leases)

    async def test_expired_and_released_view_lease_cleanup_is_idempotent(self):
        _, controller, identity = await _activate()
        now = [10.0]
        controller.issuer._clock = lambda: now[0]
        view = controller.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id=identity.module_id,
            module_epoch=identity.module_epoch,
            registry_revision=controller.registry.snapshot().revision,
            actor_id="user",
            conversation_id="conversation",
            capability_id="read",
            deadline=20.0,
        )
        lease = controller.admission.admit(view, "read")
        now[0] = 20.0

        with self.assertRaises(AdmissionError):
            controller.admission.check(lease)
        controller.admission.release(lease)
        controller.admission.release(lease)
        self.assertNotIn(lease.lease_id, controller.admission._leases)
        self.assertNotIn(view.invocation_id, controller.admission._view_leases)

        controller.issuer.release(view)
        controller.admission.release(lease)
        self.assertNotIn(lease.lease_id, controller.admission._leases)

    async def test_scheduler_admission_fails_closed_and_requires_exact_claim_proof(
        self,
    ):
        registry, controller, identity = await _activate()
        key = CollectionKey(
            "pkg/mod",
            "prices",
            1,
            "steam",
            NormalizedInput({}),
            OwnerScope.public(),
        )
        execution = ExecutionLease(
            key=key,
            token="persistent-claim-token",
            config_revision=1,
            module_epoch=identity.module_epoch,
            registry_revision=registry.snapshot().revision,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        with self.assertRaises(AdmissionError):
            controller.admission.admit_schedule(execution, "prices")

        registry2, controller2, identity2 = await _activate(
            claim_prover=lambda candidate: candidate is execution
        )
        self.assertEqual(identity2.module_epoch, identity.module_epoch)
        scheduled = controller2.admission.admit_schedule(execution, "prices")
        controller2.admission.check(scheduled)
        with self.assertRaises(AdmissionError):
            controller2.admission.admit_schedule(replace(execution), "prices")
        controller2.admission.release(scheduled)
        with self.assertRaises(AdmissionError):
            controller2.admission.check(scheduled)
        self.assertEqual(
            registry2.snapshot().module("pkg/mod").epoch, identity2.module_epoch
        )

    async def test_mutation_is_shared_and_rejects_inherited_reentry(self):
        _, controller, _ = await _activate()
        admission = controller.admission

        async def nested():
            async with admission.mutation("nested"):
                return None

        async with admission.mutation("outer"):
            child = asyncio.create_task(nested())
            with self.assertRaises(MutationReentryError):
                await child
        async with admission.mutation("after"):
            pass

    async def test_send_permit_is_scheduled_synchronously_after_final_check(self):
        _, controller, identity = await _activate()
        view = _view(controller, identity)
        lease = controller.admission.admit(view, "read")
        scheduler = _Scheduler()
        sent = []
        aborted = []

        async def final_check():
            return SendApproval("claim-1", ())

        async def sender(permit):
            sent.append(permit.permit_id)
            return "receipt"

        async def abort(approval, reason):
            aborted.append((approval.claim_id, reason))

        permit = await controller.admission.approve_and_schedule_send(
            lease,
            name="root-output",
            final_check=final_check,
            sender=sender,
            scheduler=scheduler,
            abort_before_dispatch=abort,
        )
        self.assertIsNotNone(permit)
        self.assertEqual(scheduler.permits, [(permit, "root-output")])
        await asyncio.gather(*scheduler.tasks)
        self.assertEqual(sent, [permit.permit_id])
        self.assertEqual(aborted, [])

    async def test_send_claim_is_compensated_if_gate_closes_before_dispatch(self):
        _, controller, identity = await _activate()
        view = _view(controller, identity)
        lease = controller.admission.admit(view, "read")
        scheduler = _Scheduler()
        aborted = []

        async def final_check():
            await controller.stop("pkg/mod")
            return SendApproval("claim-after-stop", ())

        async def sender(permit):
            raise AssertionError("stale lease must never start sender")

        async def abort(approval, reason):
            aborted.append((approval.claim_id, reason))

        with self.assertRaises(AdmissionError):
            await controller.admission.approve_and_schedule_send(
                lease,
                name="root-output",
                final_check=final_check,
                sender=sender,
                scheduler=scheduler,
                abort_before_dispatch=abort,
            )
        self.assertEqual(scheduler.tasks, [])
        self.assertEqual(len(aborted), 1)
        self.assertEqual(aborted[0][0], "claim-after-stop")


if __name__ == "__main__":
    unittest.main()
