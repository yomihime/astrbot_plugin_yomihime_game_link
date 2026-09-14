"""The invocation view itself is never proof of a trusted origin."""

import unittest
from dataclasses import replace

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.core.context_issuer import ContextIssuer, InvalidInvocation


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
        self.assertEqual(child.deadline, self.parent.deadline)
        self.assertEqual(child.parent_id, self.parent.invocation_id)
        self.assertIs(self.issuer.require(child), child)
        with self.assertRaises(InvalidInvocation):
            self.issuer.derive(
                self.parent, module_id="example/dota2", module_epoch=2, deadline=21
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

    def test_deadline_rechecked_and_expired_request_can_be_released(self):
        self.now = 20.0
        with self.assertRaises(InvalidInvocation):
            self.issuer.require(self.parent)
        self.issuer.release(self.parent)

    def test_release_rejects_forged_caller(self):
        with self.assertRaises(InvalidInvocation):
            self.issuer.release(replace(self.parent))
        self.assertIs(self.issuer.require(self.parent), self.parent)

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
