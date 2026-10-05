"""The invocation view itself is never proof of a trusted origin."""

import math
import unittest
from dataclasses import FrozenInstanceError, replace
from time import monotonic
from typing import get_type_hints

from ygl_test_subject.api.contexts import (
    InvocationConversationKind,
    InvocationOrigin,
    InvocationSubscriptionScope,
    InvocationView,
)
from ygl_test_subject.core.context_issuer import ContextIssuer, InvalidInvocation
from ygl_test_subject.core.ports import PublicWebBinding


class _PublicWebProofs:
    """Synthetic trusted Host composition; no token, Host or network needed."""

    def __init__(self, clock=monotonic):
        self.clock = clock
        self.minted = {}
        self.active = {}
        self.revoked = []

    def new(
        self, module_id, capability_id, *, key="bearer-one", deadline=None, generation=1
    ):
        proof = object()
        self.minted[proof] = PublicWebBinding(
            module_id,
            capability_id,
            generation,
            key,
            self.clock() + 10 if deadline is None else deadline,
        )
        return proof

    def consume(self, proof, *, module_id, capability_id, generation):
        binding = self.minted.get(proof)
        if (
            binding is None
            or proof in self.active
            or proof in self.revoked
            or (binding.module_id, binding.capability_id, binding.generation)
            != (module_id, capability_id, generation)
            or binding.deadline_monotonic <= self.clock()
        ):
            raise InvalidInvocation("synthetic host proof rejected")
        self.active[proof] = binding
        return binding

    def is_current(self, proof, binding):
        return (
            self.active.get(proof) is binding
            and binding.deadline_monotonic > self.clock()
        )

    def revoke(self, proof):
        self.active.pop(proof, None)
        self.revoked.append(proof)


class PublicWebIssuerTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.proofs = _PublicWebProofs(lambda: self.now)
        self.issuer = ContextIssuer(
            clock=lambda: self.now,
            public_web_validator=self.proofs,
            public_web_capabilities=frozenset(
                {("pkg/mod", "read"), ("pkg/child", "read")}
            ),
        )

    def _issue(self, proof):
        return self.issuer.issue_public_web(
            proof,
            module_id="pkg/mod",
            capability_id="read",
            module_epoch=1,
            registry_revision=1,
            generation=1,
            deadline=30.0,
        )

    def test_generic_issue_constructed_dto_and_foreign_proof_cannot_authorize(self):
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(
                origin=InvocationOrigin.WEB_PUBLIC,
                module_id="pkg/mod",
                module_epoch=1,
                registry_revision=1,
            )
        for proof in (
            object(),
            PublicWebBinding("pkg/mod", "read", 1, "opaque", 20.0),
            _PublicWebProofs(lambda: self.now).new("pkg/mod", "read"),
        ):
            with self.assertRaises(InvalidInvocation):
                self._issue(proof)
        proof = self.proofs.new("pkg/mod", "read", deadline=20.0)
        view = self._issue(proof)
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(replace(view))
        with self.assertRaises(InvalidInvocation):
            self._issue(proof)
        for field in (
            "actor_id",
            "conversation_id",
            "adapter_id",
            "delivery_route",
            "grant_id",
            "subscription_id",
        ):
            self.assertIsNone(getattr(view, field))
        self.assertEqual(view.deadline, 20.0)
        child = self.issuer.derive(
            view, module_id="pkg/child", module_epoch=1, capability_id="read"
        )
        self.assertIs(self.issuer.require(child), child)
        with self.assertRaises(InvalidInvocation):
            self.issuer.derive(
                view, module_id="pkg/other", module_epoch=1, capability_id="read"
            )
        self.issuer.release(view)
        self.assertIn(proof, self.proofs.revoked)
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(child)

    def test_no_deployment_expiry_and_validator_revocation_fail_closed(self):
        proof = self.proofs.new("pkg/mod", "read", deadline=20.0)
        with self.assertRaises(InvalidInvocation):
            ContextIssuer(public_web_validator=self.proofs).issue_public_web(
                proof,
                module_id="pkg/mod",
                capability_id="read",
                module_epoch=1,
                registry_revision=1,
                generation=1,
                deadline=30.0,
            )
        view = self._issue(proof)
        self.now = 20.0
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(view)
        self.issuer.release(view)
        self.now = 10.0
        proof = self.proofs.new("pkg/mod", "read")
        view = self._issue(proof)
        self.proofs.revoke(proof)
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(view)

    def test_public_session_is_only_descriptive_exact_proof_bound_data(self):
        proof = self.proofs.new("pkg/mod", "read", key="session-one")
        view = self._issue(proof)
        self.assertEqual(view.public_session_id, "session-one")
        self.assertIsNone(view.actor_id)
        self.assertIsNone(view.grant_id)
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(replace(view, public_session_id="session-two"))
        self.issuer.release(view)
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(view)
        scheduler = self.issuer.issue(
            origin=InvocationOrigin.SCHEDULER,
            module_id="pkg/mod",
            module_epoch=1,
            registry_revision=1,
        )
        self.assertIsNone(scheduler.public_session_id)
        with self.assertRaises(ValueError):
            replace(scheduler, public_session_id="forged")


class ContextIssuerTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.issuer = ContextIssuer(clock=lambda: self.now)
        self.parent = self.issuer.issue(
            origin=InvocationOrigin.LLM_TOOL,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-1",
            conversation_id="chat-1",
            adapter_id="adapter-a",
            deadline=20.0,
        )

    def test_forged_copy_and_other_issuer_are_rejected(self):
        self.assertIs(self.issuer.require(self.parent), self.parent)
        for candidate in (
            replace(self.parent),
            replace(self.parent, origin=InvocationOrigin.COMMAND),
        ):
            with self.assertRaises(InvalidInvocation):
                self.issuer.require(candidate)
        with self.assertRaises(InvalidInvocation):
            ContextIssuer(clock=lambda: self.now).require(self.parent)

    def test_child_keeps_original_source_and_deadline(self):
        child = self.issuer.derive(
            self.parent, module_id="example/dota2", module_epoch=2
        )
        self.assertEqual(child.origin, InvocationOrigin.LLM_TOOL)
        self.assertEqual(child.actor_id, self.parent.actor_id)
        self.assertEqual(child.adapter_id, self.parent.adapter_id)
        self.assertEqual(child.deadline, self.parent.deadline)
        self.assertEqual(child.parent_id, self.parent.invocation_id)
        self.assertIsNone(child.capability_id)
        self.assertIs(self.issuer.require(child), child)
        with self.assertRaises(InvalidInvocation):
            self.issuer.derive(
                self.parent, module_id="example/dota2", module_epoch=2, deadline=21
            )

    def test_capability_identity_is_issuer_bound_and_derived_explicitly(self):
        parent = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-1",
            conversation_id="chat-1",
            capability_id="inventory.lookup",
        )
        self.assertEqual(parent.capability_id, "inventory.lookup")
        self.assertIs(self.issuer.require(parent), parent)
        with self.assertRaises(InvalidInvocation):
            ContextIssuer(clock=lambda: self.now).require(parent)

        forged = replace(parent, capability_id="inventory.write")
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(forged)

        child = self.issuer.derive(
            parent,
            module_id="example/dota2",
            module_epoch=3,
            capability_id="match.lookup",
        )
        self.assertEqual(child.capability_id, "match.lookup")
        self.assertEqual(child.origin, parent.origin)
        self.assertEqual(child.actor_id, parent.actor_id)
        self.assertEqual(child.conversation_id, parent.conversation_id)
        self.assertEqual(child.module_epoch, 3)
        self.assertEqual(child.registry_revision, parent.registry_revision)
        self.assertEqual(child.deadline, parent.deadline)
        self.assertIs(self.issuer.require(child), child)

        legacy_child = self.issuer.derive(
            parent, module_id="example/dota2", module_epoch=3
        )
        self.assertIsNone(legacy_child.capability_id)

    def test_derived_grant_is_module_scoped_for_command_and_tool(self):
        command = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-1",
            conversation_id="chat-1",
            adapter_id="adapter-a",
            grant_id="grant-1",
            grant_revision=3,
            capability_id="inventory.lookup",
        )
        same_module = self.issuer.derive(
            command,
            module_id="example/steam",
            module_epoch=2,
            capability_id="inventory.lookup",
        )
        self.assertEqual(same_module.grant_id, "grant-1")
        self.assertEqual(same_module.grant_revision, 3)

        cross_module = self.issuer.derive(
            command,
            module_id="example/dota2",
            module_epoch=1,
            capability_id="match.lookup",
        )
        self.assertEqual(cross_module.origin, command.origin)
        self.assertEqual(cross_module.actor_id, command.actor_id)
        self.assertEqual(cross_module.conversation_id, command.conversation_id)
        self.assertIsNone(cross_module.grant_id)
        self.assertIsNone(cross_module.grant_revision)

        tool = self.issuer.issue(
            origin=InvocationOrigin.LLM_TOOL,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-1",
            conversation_id="chat-1",
            adapter_id="adapter-a",
            grant_id="grant-1",
            grant_revision=3,
            capability_id="inventory.lookup",
        )
        tool_child = self.issuer.derive(
            tool,
            module_id="example/dota2",
            module_epoch=1,
            capability_id="match.lookup",
        )
        self.assertEqual(tool_child.origin, tool.origin)
        self.assertEqual(tool_child.actor_id, tool.actor_id)
        self.assertIsNone(tool_child.grant_id)
        self.assertIsNone(tool_child.grant_revision)

    def test_non_chat_grant_cannot_cross_module_and_authorized_needs_grant(self):
        required = {
            "origin": InvocationOrigin.SUBSCRIPTION,
            "module_id": "example/steam",
            "module_epoch": 1,
            "registry_revision": 1,
            "actor_id": "owner-1",
            "subscription_id": "sub-1",
            "subscription_revision": 2,
            "adapter_id": "adapter-a",
            "conversation_id": "private-1",
            "delivery_route": "route-1",
            "conversation_kind": InvocationConversationKind.DIRECT,
            "subscription_scope": InvocationSubscriptionScope.AUTHORIZED,
        }
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(**required)

        authorized = self.issuer.issue(
            **required,
            grant_id="grant-1",
            grant_revision=3,
        )
        same_module = self.issuer.derive(
            authorized, module_id="example/steam", module_epoch=2
        )
        self.assertEqual(same_module.grant_id, "grant-1")
        self.assertEqual(same_module.grant_revision, 3)
        with self.assertRaises(InvalidInvocation):
            self.issuer.derive(authorized, module_id="example/dota2", module_epoch=1)

        admin = self.issuer.issue(
            origin=InvocationOrigin.ADMIN,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
            actor_id="admin-1",
            grant_id="grant-1",
            grant_revision=3,
        )
        with self.assertRaises(InvalidInvocation):
            self.issuer.derive(admin, module_id="example/dota2", module_epoch=1)

    def test_capability_id_validation_and_legacy_issue(self):
        legacy = self.issuer.issue(
            origin=InvocationOrigin.SCHEDULER,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
        )
        self.assertIsNone(legacy.capability_id)
        self.assertIs(self.issuer.require(legacy), legacy)

        for bad_id in ("", "Bad", "space id", "a/b", "bad\nvalue"):
            with self.subTest(capability_id=bad_id):
                with self.assertRaises(ValueError):
                    self.issuer.issue(
                        origin=InvocationOrigin.SCHEDULER,
                        module_id="example/steam",
                        module_epoch=1,
                        registry_revision=1,
                        capability_id=bad_id,
                    )

    def test_release_invalidates_descendants_not_other_requests(self):
        independent = self.issuer.issue(
            origin=InvocationOrigin.SCHEDULER,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
        )
        child = self.issuer.derive(
            self.parent, module_id="example/dota2", module_epoch=2
        )
        self.issuer.release(self.parent)
        for view in (self.parent, child):
            with self.assertRaises(InvalidInvocation):
                self.issuer.require(view)
        self.assertIs(self.issuer.require(independent), independent)

    def test_release_observer_sees_root_and_descendants_and_unsubscribes(self):
        child = self.issuer.derive(
            self.parent, module_id="example/dota2", module_epoch=2
        )
        grandchild = self.issuer.derive(
            child, module_id="example/dota2", module_epoch=3
        )
        observed = []

        def observer(view):
            observed.append(view.invocation_id)

        unsubscribe = self.issuer.add_release_observer(observer)
        self.issuer.attach_lease(self.parent, object())
        self.issuer.attach_lease(child, object())

        self.issuer.release(self.parent)
        self.assertEqual(
            set(observed),
            {self.parent.invocation_id, child.invocation_id, grandchild.invocation_id},
        )
        self.assertEqual(len(observed), 3)
        for view in (self.parent, child, grandchild):
            with self.assertRaises(InvalidInvocation):
                self.issuer.require(view)

        unsubscribe()
        later = self.issuer.issue(
            origin=InvocationOrigin.SCHEDULER,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
        )
        self.issuer.release(later)
        self.assertEqual(len(observed), 3)

    def test_deadline_rechecked_and_expired_request_can_be_released(self):
        self.now = 20.0
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(self.parent)
        self.issuer.release(self.parent)

    def test_lease_cleanup_accepts_expired_and_already_released_views(self):
        lease = object()
        self.issuer.attach_lease(self.parent, lease)
        self.now = 20.0

        self.assertTrue(self.issuer.detach_lease_for_cleanup(self.parent, lease))
        self.assertFalse(self.issuer.detach_lease_for_cleanup(self.parent, lease))

        self.issuer.release(self.parent)
        self.assertFalse(self.issuer.detach_lease_for_cleanup(self.parent, lease))

    def test_lease_cleanup_does_not_detach_a_replacement_sidecar(self):
        original = object()
        replacement = object()
        self.issuer.attach_lease(self.parent, original)
        self.issuer.detach_lease(self.parent, original)
        self.issuer.attach_lease(self.parent, replacement)

        with self.assertRaises(InvalidInvocation):
            self.issuer.detach_lease_for_cleanup(self.parent, original)
        self.assertIs(self.issuer.lease_for(self.parent), replacement)

    def test_release_rejects_forged_caller(self):
        with self.assertRaises(InvalidInvocation):
            self.issuer.release(replace(self.parent))
        self.assertIs(self.issuer.require(self.parent), self.parent)

    def test_adapter_scope_is_issuer_bound_and_required_for_identity(self):
        self.assertIs(self.issuer.require_adapter(self.parent), self.parent)
        self.assertTrue(get_type_hints(ContextIssuer.require_adapter))
        forged = replace(self.parent, adapter_id="adapter-b")
        with self.assertRaises(InvalidInvocation):
            self.issuer.require_adapter(forged)

        legacy = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-1",
            conversation_id="chat-1",
        )
        self.assertIs(self.issuer.require(legacy), legacy)
        with self.assertRaises(InvalidInvocation):
            self.issuer.require_adapter(legacy)

    def test_adapter_id_is_validated_and_derive_has_no_replacement_parameter(self):
        with self.assertRaises((TypeError, ValueError)):
            self.issuer.issue(
                origin=InvocationOrigin.COMMAND,
                module_id="example/steam",
                module_epoch=1,
                registry_revision=1,
                actor_id="user-1",
                conversation_id="chat-1",
                adapter_id="adapter\nforged",
            )
        self.assertEqual(
            set(InvocationView.__dataclass_fields__),
            {
                "invocation_id",
                "origin",
                "actor_id",
                "conversation_id",
                "module_id",
                "module_epoch",
                "registry_revision",
                "deadline",
                "parent_id",
                "grant_id",
                "grant_revision",
                "subscription_id",
                "subscription_revision",
                "adapter_id",
                "capability_id",
                "delivery_route",
                "conversation_kind",
                "subscription_scope",
                "public_session_id",
            },
        )

    def test_anonymous_chat_context_cannot_be_issued(self):
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(
                origin=InvocationOrigin.COMMAND,
                module_id="example/steam",
                module_epoch=1,
                registry_revision=1,
            )

    def test_private_scheduler_requires_owner(self):
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(
                origin=InvocationOrigin.SCHEDULER,
                module_id="example/steam",
                module_epoch=1,
                registry_revision=1,
                grant_id="grant-1",
                grant_revision=1,
            )

    def test_subscription_evaluation_requires_owner(self):
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(
                origin=InvocationOrigin.SCHEDULER,
                module_id="example/steam",
                module_epoch=1,
                registry_revision=1,
                subscription_id="subscription-1",
                subscription_revision=1,
            )

    def test_subscription_origin_requires_issuer_bound_complete_route(self):
        required = {
            "origin": InvocationOrigin.SUBSCRIPTION,
            "module_id": "example/steam",
            "module_epoch": 1,
            "registry_revision": 1,
            "actor_id": "owner-1",
            "subscription_id": "sub-1",
            "subscription_revision": 2,
            "adapter_id": "adapter-a",
            "conversation_id": "private-1",
            "delivery_route": "route-1",
            "conversation_kind": InvocationConversationKind.DIRECT,
            "subscription_scope": InvocationSubscriptionScope.PUBLIC,
        }
        view = self.issuer.issue(**required)
        self.assertEqual(view.origin, InvocationOrigin.SUBSCRIPTION)
        self.assertEqual(view.actor_id, "owner-1")
        self.assertEqual(view.delivery_route, "route-1")
        self.assertIs(self.issuer.require(view), view)
        child = self.issuer.derive(view, module_id="example/steam", module_epoch=1)
        self.assertEqual(child.origin, InvocationOrigin.SUBSCRIPTION)
        self.assertEqual(child.delivery_route, "route-1")
        self.assertEqual(child.subscription_revision, 2)

        for omitted in (
            "actor_id",
            "subscription_id",
            "subscription_revision",
            "adapter_id",
            "conversation_id",
            "delivery_route",
            "conversation_kind",
            "subscription_scope",
        ):
            with self.subTest(omitted=omitted), self.assertRaises(InvalidInvocation):
                self.issuer.issue(
                    **{key: value for key, value in required.items() if key != omitted}
                )
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(
                origin=InvocationOrigin.SCHEDULER,
                module_id="example/steam",
                module_epoch=1,
                registry_revision=1,
                actor_id="owner-1",
                subscription_id="sub-1",
                subscription_revision=2,
                adapter_id="adapter-a",
                conversation_id="private-1",
                delivery_route="route-1",
                conversation_kind=InvocationConversationKind.DIRECT,
                subscription_scope=InvocationSubscriptionScope.PUBLIC,
            )
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(replace(view, origin=InvocationOrigin.SCHEDULER))
        public_group = self.issuer.issue(
            **{
                **required,
                "conversation_kind": InvocationConversationKind.GROUP,
            }
        )
        self.assertIs(self.issuer.require(public_group), public_group)
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(
                **{
                    **required,
                    "conversation_kind": InvocationConversationKind.GROUP,
                    "subscription_scope": InvocationSubscriptionScope.USER,
                }
            )
        with self.assertRaises(InvalidInvocation):
            self.issuer.issue(
                **{
                    **required,
                    "conversation_kind": InvocationConversationKind.GROUP,
                    "subscription_scope": InvocationSubscriptionScope.AUTHORIZED,
                    "grant_id": "grant-1",
                    "grant_revision": 1,
                }
            )


class InvocationViewContractTests(unittest.TestCase):
    def test_valid_context_and_optional_references(self) -> None:
        view = InvocationView(
            "request.1",
            InvocationOrigin.SCHEDULER,
            None,
            "conversation.1",
            "yomihime/catalog",
            2,
            3,
            12.5,
            grant_id="grant.1",
            grant_revision=4,
            subscription_id="sub.1",
            subscription_revision=5,
        )
        self.assertEqual(12.5, view.deadline)
        with self.assertRaises(FrozenInstanceError):
            view.origin = InvocationOrigin.COMMAND  # type: ignore[misc]

    def test_context_rejects_forged_values_and_unpaired_references(self) -> None:
        base = (
            "5c2da6e2-1d19-4f08-ab24-62eff2810024",
            InvocationOrigin.COMMAND,
            "123456",
            None,
            "yomihime/catalog",
            1,
            1,
        )
        for deadline in (0, -1.0, math.inf, math.nan, True):
            with self.subTest(deadline=deadline):
                with self.assertRaises((TypeError, ValueError)):
                    InvocationView(*base, deadline=deadline)
        with self.assertRaises(TypeError):
            InvocationView(*base, module_epoch=True)
        with self.assertRaises(ValueError):
            InvocationView(*base, grant_id="grant.1")
        with self.assertRaises(ValueError):
            InvocationView(*base, subscription_revision=1)
        with self.assertRaises(ValueError):
            InvocationView("bad\x00id", *base[1:])
        for module_id in ("pkg./module", "pkg/module.", "pkg-/module"):
            with self.subTest(module_id=module_id):
                with self.assertRaises(ValueError):
                    InvocationView(*base[:4], module_id, *base[5:])
