"""Opaque page grants bind Host session, lifetime and closed declared fields."""

import unittest
from dataclasses import replace

from ygl_test_subject.adapters.astrbot.page_sessions import HostModulePageSessions
from ygl_test_subject.core.contracts.administration import AdminAuthorizationDenied
from ygl_test_subject.services.module_pages import ModulePageGrant


class PageSessionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.registry = HostModulePageSessions(wall_clock=lambda: self.now)
        self.binding = ModulePageGrant(
            "sample/demo",
            "config",
            "runtime-a",
            2,
            frozenset({"ordinary"}),
            frozenset({"credential"}),
        )
        self.authority = dict(
            subject="synthetic-admin",
            bearer_digest=self.registry.bearer_digest("synthetic-session"),
            binding=self.binding,
            generation=1,
        )

    def issue(self, **kwargs):
        return self.registry.issue(**self.authority, expiry=1000, **kwargs)[
            "page_session"
        ]

    def require(self, handle, **kwargs):
        return self.registry.require(handle, **(self.authority | kwargs))

    def test_rotation_invalidates_only_same_view_and_parallel_reads_remain_valid(self):
        first, other = self.issue(), self.issue()
        rotated = self.issue(previous_session=first)
        with self.assertRaises(AdminAuthorizationDenied):
            self.require(first)
        self.require(other)
        self.require(rotated)

    def test_subject_and_actual_bearer_are_both_bound(self):
        handle = self.issue()
        for overrides in (
            {"subject": "other"},
            {"bearer_digest": self.registry.bearer_digest("another-session")},
        ):
            with self.assertRaises(AdminAuthorizationDenied):
                self.require(handle, **overrides)

    def test_epoch_runtime_page_and_field_policy_changes_reject(self):
        handle = self.issue()
        changes = (
            {"module_epoch": 3},
            {"runtime_id": "runtime-b"},
            {"page": "home"},
            {"owner": "other/demo"},
            {"config_fields": frozenset()},
            {"credential_fields": frozenset()},
        )
        for changeset in changes:
            with self.assertRaises(AdminAuthorizationDenied):
                self.require(handle, binding=replace(self.binding, **changeset))
        with self.assertRaises(AdminAuthorizationDenied):
            self.require(handle, generation=2)

    def test_expiry_has_thirty_second_upper_bound_and_cleanup_is_bounded(self):
        handle = self.issue()
        self.now = 130.0
        with self.assertRaises(AdminAuthorizationDenied):
            self.require(handle)
        self.assertEqual(len(self.registry._sessions), 0)
        for _ in range(128):
            self.issue()
        with self.assertRaises(AdminAuthorizationDenied):
            self.issue()
        self.now += 31
        self.issue()
        self.assertEqual(len(self.registry._sessions), 1)

    def test_dispose_reentry_and_owner_revoke_deny_old_proofs(self):
        old = self.issue()
        self.registry.revoke(
            old,
            subject=self.authority["subject"],
            bearer_digest=self.authority["bearer_digest"],
        )
        new = self.issue()
        with self.assertRaises(AdminAuthorizationDenied):
            self.require(old)
        self.require(new)
        self.registry.revoke_owner(self.binding.owner)
        with self.assertRaises(AdminAuthorizationDenied):
            self.require(new)

    def test_other_session_cannot_close_or_rotate_existing_view(self):
        handle = self.issue()
        with self.assertRaises(AdminAuthorizationDenied):
            self.registry.revoke(
                handle, subject="other", bearer_digest=self.authority["bearer_digest"]
            )
        with self.assertRaises(AdminAuthorizationDenied):
            self.registry.issue(
                **(self.authority | {"subject": "other"}),
                expiry=120,
                previous_session=handle,
            )
        self.require(handle)

    def test_close_fences_all_old_grants_and_never_reopens(self):
        handle = self.issue()
        self.registry.close()
        with self.assertRaises(AdminAuthorizationDenied):
            self.require(handle)
        with self.assertRaises(AdminAuthorizationDenied):
            self.issue()

    def test_repeated_rotation_preserves_lineage_and_immediately_retires_old_proof(
        self,
    ):
        proof = self.issue()
        lineage = self.require(proof).lineage_id
        other = self.issue()
        other_lineage = self.require(other).lineage_id
        self.assertNotEqual(lineage, other_lineage)
        for elapsed in (20, 40, 60):
            self.now = 100.0 + elapsed
            previous = proof
            proof = self.issue(previous_session=previous)
            self.assertEqual(self.require(proof).lineage_id, lineage)
            self.assertEqual(self.require(proof).expiry, self.now + 30)
            with self.assertRaises(AdminAuthorizationDenied):
                self.require(previous)
        self.assertEqual(len(self.registry._sessions), 1)

    def test_expired_proof_cannot_rotate_or_transfer_lineage_to_a_new_view(self):
        proof = self.issue()
        lineage = self.require(proof).lineage_id
        self.now += 30
        with self.assertRaises(AdminAuthorizationDenied):
            self.issue(previous_session=proof)
        fresh = self.issue()
        self.assertNotEqual(self.require(fresh).lineage_id, lineage)
        with self.assertRaises(AdminAuthorizationDenied):
            self.require(proof)

    def test_binding_changes_cannot_inherit_a_views_lineage(self):
        proof = self.issue()
        lineage = self.require(proof).lineage_id
        for binding in (
            replace(self.binding, page="other"),
            replace(self.binding, credential_fields=frozenset({"another"})),
            replace(self.binding, runtime_id="runtime-b"),
        ):
            with (
                self.subTest(binding=binding),
                self.assertRaises(AdminAuthorizationDenied),
            ):
                self.registry.issue(
                    **(self.authority | {"binding": binding}),
                    expiry=1000,
                    previous_session=proof,
                )
        self.assertEqual(self.require(proof).lineage_id, lineage)
