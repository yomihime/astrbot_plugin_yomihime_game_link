import unittest

from ygl_test_subject.core.collection_context import require_collection_context
from ygl_test_subject.core.context_issuer import ContextIssuer, InvalidInvocation
from ygl_test_subject.core.contracts.validation_boundary import validate_contract

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.storage import GrantReference, OwnerScope
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    CollectionView,
    NormalizedInput,
)


class _RecordingBinder:
    def __init__(self) -> None:
        self.received = []

    async def bind(self, invocation):
        self.received.append(invocation)
        return object()


class CollectorContextContractTests(unittest.TestCase):
    MODULE = "example/steam"

    def setUp(self) -> None:
        self.now = 100.0
        self.issuer = ContextIssuer(clock=lambda: self.now)

    def key(self, scope):
        return validate_contract(
            CollectionKey(
                self.MODULE,
                "prices",
                1,
                "steam",
                validate_contract(NormalizedInput({"app": 1})),
                scope,
            )
        )

    def issue(self, **kwargs):
        values = {
            "origin": InvocationOrigin.SCHEDULER,
            "module_id": self.MODULE,
            "module_epoch": 1,
            "registry_revision": 1,
        }
        values.update(kwargs)
        return self.issuer.issue(**values)

    def test_cc01_public_scheduler_view_is_bound_as_same_object(self):
        invocation = self.issue()
        context = validate_contract(
            CollectionView(self.key(OwnerScope.public()), invocation=invocation)
        )
        binder = _RecordingBinder()

        validated = require_collection_context(
            self.issuer, context, clock=lambda: self.now
        )

        import asyncio

        asyncio.run(binder.bind(validated))
        self.assertIs(validated, invocation)
        self.assertIs(binder.received[0], invocation)

    def test_cc02_forged_copy_and_missing_invocation_are_rejected(self):
        invocation = self.issue()
        context = validate_contract(
            CollectionView(self.key(OwnerScope.public()), invocation=invocation)
        )
        forged = type(invocation)(
            invocation_id=invocation.invocation_id,
            origin=invocation.origin,
            actor_id=invocation.actor_id,
            conversation_id=invocation.conversation_id,
            module_id=invocation.module_id,
            module_epoch=invocation.module_epoch,
            registry_revision=invocation.registry_revision,
            deadline=invocation.deadline,
            parent_id=invocation.parent_id,
            grant_id=invocation.grant_id,
            grant_revision=invocation.grant_revision,
            subscription_id=invocation.subscription_id,
            subscription_revision=invocation.subscription_revision,
        )
        with self.assertRaises(InvalidInvocation):
            require_collection_context(
                self.issuer,
                validate_contract(CollectionView(context.key, invocation=forged)),
            )
        with self.assertRaises(InvalidInvocation):
            require_collection_context(
                self.issuer, validate_contract(CollectionView(context.key))
            )

    def test_cc03_module_owner_and_grant_mismatches_are_rejected(self):
        owner = "owner-1"
        grant = validate_contract(GrantReference("grant-1", 2))
        cases = (
            self.issue(module_id="example/hbr"),
            self.issue(actor_id="other"),
            self.issue(actor_id=owner, grant_id="other", grant_revision=2),
            self.issue(actor_id=owner, grant_id=grant.grant_id, grant_revision=1),
        )
        scopes = (
            OwnerScope.public(),
            OwnerScope.user(owner),
            OwnerScope.authorized(owner, grant),
            OwnerScope.authorized(owner, grant),
        )
        for invocation, scope in zip(cases, scopes):
            with (
                self.subTest(invocation=invocation, scope=scope),
                self.assertRaises(InvalidInvocation),
            ):
                require_collection_context(
                    self.issuer,
                    validate_contract(
                        CollectionView(self.key(scope), invocation=invocation)
                    ),
                )

    def test_cc04_non_scheduler_and_public_private_grant_are_rejected(self):
        command = self.issue(
            origin=InvocationOrigin.COMMAND,
            actor_id="owner",
            conversation_id="chat",
        )
        with self.assertRaises(InvalidInvocation):
            require_collection_context(
                self.issuer,
                validate_contract(
                    CollectionView(self.key(OwnerScope.public()), invocation=command)
                ),
            )
        private = self.issue(actor_id="owner", grant_id="grant-1", grant_revision=1)
        with self.assertRaises(InvalidInvocation):
            require_collection_context(
                self.issuer,
                validate_contract(
                    CollectionView(self.key(OwnerScope.public()), invocation=private)
                ),
            )

    def test_cc05_deadline_cannot_extend_expire_or_survive_release(self):
        invocation = self.issue(deadline=110.0)
        with self.assertRaises(InvalidInvocation):
            require_collection_context(
                self.issuer,
                validate_contract(
                    CollectionView(self.key(OwnerScope.public()), invocation=invocation)
                ),
                clock=lambda: self.now,
            )
        with self.assertRaises(InvalidInvocation):
            require_collection_context(
                self.issuer,
                validate_contract(
                    CollectionView(
                        self.key(OwnerScope.public()),
                        deadline_monotonic=111.0,
                        invocation=invocation,
                    )
                ),
            )
        context = validate_contract(
            CollectionView(
                self.key(OwnerScope.public()),
                deadline_monotonic=105.0,
                invocation=invocation,
            )
        )
        self.assertIs(
            require_collection_context(self.issuer, context, clock=lambda: self.now),
            invocation,
        )
        self.now = 105.0
        with self.assertRaises(InvalidInvocation):
            require_collection_context(self.issuer, context, clock=lambda: self.now)
        self.issuer.release(invocation)
        with self.assertRaises(InvalidInvocation):
            require_collection_context(self.issuer, context, clock=lambda: 100.0)

    def test_cc06_legacy_collection_view_is_constructible_but_not_authorized(self):
        context = validate_contract(CollectionView(self.key(OwnerScope.public())))
        with self.assertRaises(InvalidInvocation):
            require_collection_context(self.issuer, context)


if __name__ == "__main__":
    unittest.main()
