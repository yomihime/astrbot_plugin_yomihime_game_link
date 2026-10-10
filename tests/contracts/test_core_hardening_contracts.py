"""Frozen internal DTO and port checks for the core hardening contract."""

from __future__ import annotations

import dataclasses
import inspect
import unittest
from datetime import datetime
from typing import get_type_hints

from ygl_test_subject.core import ports
from ygl_test_subject.core.contracts.administration import AdminAuthorizationGrant
from ygl_test_subject.core.contracts.services import PersistedConfigPatch

import yomihime_game_link_sdk
from yomihime_game_link_sdk.contexts import (
    InvocationConversationKind,
    InvocationSubscriptionScope,
    InvocationView,
)
from yomihime_game_link_sdk.services import ConfigSnapshot, HealthReport


class CoreHardeningContractTests(unittest.TestCase):
    def test_port_dtos_are_frozen_and_annotations_resolve(self) -> None:
        for dto in (
            ports.RunIdentity,
            ports.DependencyIdentity,
            ports.AdmissionLease,
            ports.ScheduledLease,
            ports.DeliveryMemberIdentity,
            ports.DeliveryLease,
            ports.SendApproval,
            ports.SendPermit,
        ):
            with self.subTest(dto=dto.__name__):
                self.assertTrue(dataclasses.is_dataclass(dto))
                self.assertTrue(dto.__dataclass_params__.frozen)
                get_type_hints(dto)

        for protocol, methods in (
            (
                ports.AdmissionPort,
                (
                    "admit",
                    "admit_schedule",
                    "admit_delivery",
                    "check",
                    "release",
                    "activate",
                    "close",
                    "mutation",
                    "approve_and_schedule_send",
                ),
            ),
            (ports.ApprovedSendScheduler, ("schedule",)),
        ):
            for method_name in methods:
                with self.subTest(protocol=protocol.__name__, method=method_name):
                    get_type_hints(getattr(protocol, method_name))

        self.assertIs(get_type_hints(ports.AdmissionPort.admit)["view"], InvocationView)
        self.assertIs(
            get_type_hints(ports.AdmissionPort.activate)["health"], HealthReport
        )
        self.assertEqual(
            get_type_hints(ports.AdmissionPort.approve_and_schedule_send)["return"],
            ports.SendPermit | None,
        )

    def test_leases_reject_bool_versions_and_keep_immutable_snapshots(self) -> None:
        dependency = ports.DependencyIdentity(
            module_id="demo/dependency",
            module_epoch=2,
            capability_id="demo.dependency.read",
            health_revision=0,
        )
        dependencies = [dependency]
        lease = ports.AdmissionLease(
            lease_id="lease-1",
            invocation_id="invocation-1",
            module_id="demo/module",
            module_epoch=1,
            capability_id="demo.module.read",
            health_revision=0,
            registry_revision=0,
            dependencies=dependencies,
        )
        dependencies.clear()
        self.assertEqual(lease.dependencies, (dependency,))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            lease.module_epoch = 2

        invalid_values = (
            (
                ports.RunIdentity,
                {
                    "runtime_id": "runtime",
                    "module_id": "demo/module",
                    "module_epoch": True,
                },
            ),
            (
                ports.DependencyIdentity,
                {
                    "module_id": "demo/dependency",
                    "module_epoch": 1,
                    "capability_id": "demo.read",
                    "health_revision": True,
                },
            ),
            (
                ports.AdmissionLease,
                {
                    "lease_id": "l",
                    "invocation_id": "i",
                    "module_id": "demo/module",
                    "module_epoch": 1,
                    "capability_id": "demo.read",
                    "health_revision": 0,
                    "registry_revision": True,
                    "dependencies": (),
                },
            ),
        )
        for dto, fields in invalid_values:
            with self.subTest(dto=dto.__name__):
                with self.assertRaises(ValueError):
                    dto(**fields)

    def test_delivery_lease_checks_member_scope_and_digest_uniqueness(self) -> None:
        member = ports.DeliveryMemberIdentity(
            event_key="event-1",
            event_version=1,
            subscription_id="subscription-1",
            subscription_revision=3,
            owner_id="user-1",
            module_id="demo/module",
            grant_id="grant-1",
            grant_revision=2,
            adapter_id="test-adapter",
            conversation_id="conversation-1",
            delivery_route="trusted-route-1",
            conversation_kind=InvocationConversationKind.DIRECT,
            subscription_scope=InvocationSubscriptionScope.AUTHORIZED,
        )
        lease = ports.DeliveryLease(
            lease_id="delivery-lease-1",
            work_id="event-1:1",
            kind=ports.DeliveryWorkKind.EVENT,
            module_id="demo/module",
            module_epoch=4,
            registry_revision=0,
            collector_id="demo.collector",
            key_version=1,
            dependencies=(),
            members=(member,),
        )
        self.assertEqual(lease.members, (member,))

        with self.assertRaises(ValueError):
            ports.DeliveryMemberIdentity(
                **{
                    **dataclasses.asdict(member),
                    "conversation_kind": InvocationConversationKind.GROUP,
                }
            )
        with self.assertRaises(ValueError):
            ports.DeliveryLease(
                lease_id="digest-lease",
                work_id="digest-1",
                kind=ports.DeliveryWorkKind.DIGEST,
                module_id="demo/module",
                module_epoch=4,
                registry_revision=0,
                collector_id="demo.collector",
                key_version=1,
                dependencies=(),
                members=(member, member),
            )

    def test_config_authorized_port_is_internal_and_sdk_identity_is_unchanged(
        self,
    ) -> None:
        hints = get_type_hints(ports.ConfigRepository.update_authorized)
        self.assertIs(hints["grant"], AdminAuthorizationGrant)
        self.assertIs(hints["patch"], PersistedConfigPatch)
        self.assertIs(hints["return"], ConfigSnapshot)
        self.assertEqual(
            tuple(
                inspect.signature(ports.ConfigRepository.update_authorized).parameters
            ),
            ("self", "target", "patch", "grant"),
        )
        self.assertTrue(callable(ports.ConfigRepository.update))
        self.assertIs(ports.InvocationView, InvocationView)
        self.assertIs(ports.InvocationConversationKind, InvocationConversationKind)
        self.assertIs(ports.InvocationSubscriptionScope, InvocationSubscriptionScope)
        self.assertIs(ports.HealthReport, HealthReport)
        self.assertFalse(hasattr(yomihime_game_link_sdk, "AdmissionLease"))
        self.assertFalse(hasattr(yomihime_game_link_sdk, "AdmissionPort"))

    def test_send_abort_and_scheduler_probe_are_additive_repository_ports(self) -> None:
        method_expectations = (
            (
                ports.RootOutputRepository,
                "abort_before_dispatch",
                ("self", "claim", "completed_at", "error_code"),
                {
                    "claim": ports.RootOutputClaim,
                    "completed_at": datetime,
                    "error_code": str,
                    "return": ports.RootOutputClaim | None,
                },
            ),
            (
                ports.DigestWindowRepository,
                "abort_envelope_send",
                ("self", "claim", "attempt", "expected_revision"),
                {
                    "claim": ports.DigestEnvelopeClaim,
                    "attempt": ports.DeliveryAttempt,
                    "expected_revision": int,
                    "return": ports.DigestEnvelope | None,
                },
            ),
            (
                ports.SchedulerRepository,
                "is_current",
                ("self", "lease", "now"),
                {
                    "lease": ports.ExecutionLease,
                    "now": datetime,
                    "return": bool,
                },
            ),
        )
        for (
            protocol,
            method_name,
            expected_parameters,
            expected_hints,
        ) in method_expectations:
            method = getattr(protocol, method_name)
            with self.subTest(protocol=protocol.__name__, method=method_name):
                self.assertEqual(
                    tuple(inspect.signature(method).parameters), expected_parameters
                )
                hints = get_type_hints(method)
                for name, expected_type in expected_hints.items():
                    self.assertEqual(hints[name], expected_type)

        self.assertEqual(
            tuple(inspect.signature(ports.RootOutputRepository.complete).parameters),
            ("self", "claim", "receipt", "outcome", "completed_at", "error_code"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    ports.DigestWindowRepository.complete_envelope_send
                ).parameters
            ),
            ("self", "claim", "attempt", "expected_revision", "retry_at"),
        )

    def test_private_cache_resource_deadline_ports_are_internal(self) -> None:
        expectations = (
            (
                ports.CacheRepository,
                "put",
                (
                    "self",
                    "request",
                    "entry",
                    "expected_revision",
                    "authorization_deadline",
                ),
                {"authorization_deadline": datetime | None},
            ),
            (
                ports.CacheRepository,
                "invalidate",
                ("self", "request", "authorization_deadline"),
                {"authorization_deadline": datetime | None},
            ),
            (
                ports.ResourceRepository,
                "register",
                ("self", "metadata", "expected_revision", "authorization_deadline"),
                {"authorization_deadline": datetime | None},
            ),
            (
                ports.ResourceRepository,
                "delete",
                ("self", "asset_id", "scope", "authorization_deadline"),
                {"authorization_deadline": datetime | None},
            ),
            (
                ports.ResourceRepository,
                "registration_state",
                ("self", "asset_id", "scope"),
                {"return": yomihime_game_link_sdk.storage.ResourceMetadata | None},
            ),
        )
        for protocol, method_name, parameters, hints in expectations:
            method = getattr(protocol, method_name)
            with self.subTest(protocol=protocol.__name__, method=method_name):
                self.assertEqual(
                    tuple(inspect.signature(method).parameters), parameters
                )
                actual_hints = get_type_hints(method)
                for name, expected_type in hints.items():
                    self.assertEqual(actual_hints[name], expected_type)
        self.assertIs(ports.AuthorizationWindowExpired.__bases__[0], PermissionError)
        self.assertFalse(hasattr(yomihime_game_link_sdk, "AuthorizationWindowExpired"))


if __name__ == "__main__":
    unittest.main()
