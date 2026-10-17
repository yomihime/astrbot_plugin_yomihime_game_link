"""B's offline authority/resource tests; no sender, Host or production wiring."""

from __future__ import annotations

import asyncio
import copy
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from ygl_test_subject.core.admission import MutationReentryError
from ygl_test_subject.core.contracts.services import GrantStatus
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.services.generated_png import (
    GeneratedPngCoordinator,
    GeneratedPngError,
    GeneratedPngLimits,
)

from tests.fixtures.generated_png import GeneratedPngFixture
from tests.presentation.test_png_validation import png_bitmap, replace_idat
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import DisplayAudience, Privacy
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from yomihime_game_link_sdk.services import ConfigSnapshot, ConfigTarget
from yomihime_game_link_sdk.storage import OwnershipKind
from yomihime_game_link_sdk.subscriptions import ConversationKind


class GeneratedPngTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = GeneratedPngFixture()
        self.addAsyncCleanup(self.f.stop)
        await self.f.start()
        self.c = self.f.coordinator
        self.bitmap = png_bitmap()
        self.target = await self.f.output._target(self.f.view, self.f.result.privacy)

    def new_coordinator(self, **limits):
        coordinator = self.f.new_coordinator(replace(GeneratedPngLimits(), **limits))
        self.f.coordinators.append(coordinator)
        return coordinator

    async def registered(self, coordinator=None, *, view=None, result=None):
        coordinator = coordinator or self.c
        context = await coordinator.bind(view or self.f.view, result or self.f.result)
        reference = await coordinator.register(context, self.bitmap)
        return context, reference

    def pin(self, context, reference, *, coordinator=None, **changes):
        args = dict(
            invocation=self.f.view,
            result=self.f.result,
            expected_target=self.target,
            audience=DisplayAudience.PUBLIC,
        )
        args.update(changes)
        return (coordinator or self.c).pin(context, reference, **args)

    async def test_public_actor_is_user_owned_and_read_once_has_no_send_effect(self):
        context, reference = await self.registered()
        entry = self.c._entries[context]
        self.assertIs(entry.ownership, OwnershipKind.USER)
        self.assertEqual(entry.principal, "principal-SENSITIVE")
        async with self.pin(context, reference) as lease:
            self.assertIs(await lease.read_once(), self.bitmap.content)
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()
            await self.c.revalidate(lease)
            self.assertEqual(
                self.c._conversion_bytes, 3 * 4 * ((len(self.bitmap.content) + 2) // 3)
            )
        self.assertEqual(self.c._conversion_bytes, 0)
        self.c.release(context)
        self.assertEqual((len(self.c._entries), self.c._raw_bytes), (0, 0))
        with closing(self.f.db.connect()) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM b04_root_outputs").fetchone()[
                    0
                ],
                0,
            )

    async def test_exact_context_ref_invocation_result_and_assertions(self):
        context, reference = await self.registered()
        for changed in (
            dict(invocation=replace(self.f.view)),
            dict(result=replace(self.f.result)),
            dict(result=replace(self.f.result, result_id="wrong-result")),
            dict(audience=DisplayAudience.PRIVATE),
            dict(expected_target=replace(self.target, recipient_id="wrong-actor")),
        ):
            with self.subTest(changed=tuple(changed)):
                with self.assertRaises(GeneratedPngError):
                    async with self.pin(context, reference, **changed):
                        self.fail("invalid authority yielded a pin")
        with self.assertRaises(GeneratedPngError):
            await self.c.register(type(context)(), self.bitmap)
        with self.assertRaises(GeneratedPngError):
            async with self.pin(context, replace(reference)):
                self.fail("clone reference accepted")
        other = self.new_coordinator()
        with self.assertRaises(GeneratedPngError):
            await other.register(context, self.bitmap)
        async with self.pin(context, reference) as lease:
            clone = copy.copy(lease)
            with self.assertRaises(GeneratedPngError):
                await clone.read_once()

    async def test_reconstructed_view_help_tool_nested_and_missing_actor_denied(self):
        with self.assertRaises(GeneratedPngError):
            await self.c.bind(replace(self.f.view), self.f.result)
        tool = self.f.new_view(origin=InvocationOrigin.LLM_TOOL)
        help_view = self.f.new_view(capability_id=None)
        child = self.f.issuer.derive(
            self.f.view,
            module_id="sample/demo",
            module_epoch=self.f.view.module_epoch,
            capability_id="lookup",
        )
        for view in (tool, help_view, child):
            with self.assertRaises(GeneratedPngError):
                await self.c.bind(view, self.f.result)
        # An actual no-actor issued context is non-command and cannot bind.
        no_actor = self.f.new_view(origin=InvocationOrigin.SCHEDULER, actor_id=None)
        with self.assertRaises(GeneratedPngError):
            await self.c.bind(no_actor, self.f.result)
        self.assertFalse(self.c._entries)

    async def test_missing_document_and_private_without_grant_denied(self):
        error = validate_contract(
            CapabilityResult(
                "error-result",
                ResultStatus.ERROR,
                error=ErrorDetail(ErrorCode.MODULE_UNAVAILABLE, "safe"),
            )
        )
        for result in (
            replace(self.f.result, document=None),
            self.f.new_result(Privacy.PRIVATE),
            error,
        ):
            with self.assertRaises(GeneratedPngError):
                await self.c.bind(self.f.view, result)
        self.assertFalse(self.c._entries)

    async def test_grant_scope_current_status_revision_owner_module_and_expiry(self):
        grant = self.f.grant()
        view = self.f.new_view(grant_id=grant.grant_id, grant_revision=grant.revision)
        result = self.f.new_result(Privacy.PRIVATE)
        context, reference = await self.registered(view=view, result=result)
        self.assertIs(self.c._entries[context].ownership, OwnershipKind.AUTHORIZED)
        target = await self.f.output._target(view, Privacy.PRIVATE)
        mutations = (
            dict(status=GrantStatus.REVOKED),
            dict(status=GrantStatus.EXPIRED),
            dict(status=GrantStatus.UNAVAILABLE),
            dict(revision=2),
            dict(principal_id="other-principal"),
            dict(module_id="other/module"),
            dict(grant_id="other-grant"),
            dict(expires_at=self.f.utc),
        )
        for mutation in mutations:
            async with self.f.admission.mutation("test:grant-mutation"):
                self.f.grants.value = replace(grant, **mutation)
            with self.assertRaises(GeneratedPngError):
                await self.c.bind(view, result)
        async with self.f.admission.mutation("test:grant-restore"):
            self.f.grants.value = grant
        async with self.c.pin(
            context,
            reference,
            invocation=view,
            result=result,
            expected_target=target,
            audience=DisplayAudience.PRIVATE,
        ) as lease:
            async with self.f.admission.mutation("test:grant-revoke"):
                self.f.grants.value = replace(grant, status=GrantStatus.REVOKED)
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()

    async def test_private_group_and_granted_public_group_denied_public_group_allowed(
        self,
    ):
        async with self.f.admission.mutation("test:group-route"):
            self.f.routes.value = replace(
                self.f.route,
                kind=ConversationKind.GROUP,
                delivery_route="group:SENSITIVE",
            )
        public_context = await self.c.bind(self.f.view, self.f.result)
        self.assertIs(self.c._entries[public_context].ownership, OwnershipKind.USER)
        grant = self.f.grant()
        view = self.f.new_view(grant_id=grant.grant_id, grant_revision=1)
        for result in (self.f.result, self.f.new_result(Privacy.PRIVATE)):
            with self.assertRaises(GeneratedPngError):
                await self.c.bind(view, result)

    async def test_owner_proof_required_and_revocation_blocks_future_read(self):
        owner_fixture = GeneratedPngFixture()
        try:
            await owner_fixture.start(owner=True)
            c = owner_fixture.coordinator
            context = await c.bind(owner_fixture.view, owner_fixture.result)
            reference = await c.register(context, self.bitmap)
            target = await owner_fixture.output._target(
                owner_fixture.view, Privacy.PRIVATE
            )
            async with c.pin(
                context,
                reference,
                invocation=owner_fixture.view,
                result=owner_fixture.result,
                expected_target=target,
                audience=DisplayAudience.PRIVATE,
            ) as lease:
                owner_fixture.owner.release(owner_fixture.view)
                with self.assertRaises(GeneratedPngError):
                    await lease.read_once()
            with self.assertRaises(GeneratedPngError):
                await c.bind(owner_fixture.view, owner_fixture.result)
        finally:
            await owner_fixture.stop()

    async def test_principal_and_route_changes_deny_revalidation(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            # Offline port exposes controlled changes; real identity mappings
            # are append-only. This exercises captured principal comparison.
            original = self.f.identity_reads.repository

            class OtherPrincipal:
                async def find_principal(inner, *args):
                    principal = await original.find_principal(*args)
                    return replace(principal, principal_id="other-principal")

            self.f.identity_reads.repository = OtherPrincipal()
            with self.assertRaises(GeneratedPngError):
                await self.c.revalidate(lease)
            self.f.identity_reads.repository = original
            async with self.f.admission.mutation("test:changed-route"):
                self.f.routes.value = replace(
                    self.f.route, delivery_route="direct:other"
                )
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()

    async def test_real_issuer_release_retains_pin_but_denies_new_reads(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            self.f.issuer.release(self.f.view)
            self.assertEqual(self.c._raw_bytes, len(self.bitmap.content))
            self.assertEqual(len(self.c._entries), 1)
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()
        self.assertEqual(
            (len(self.c._entries), self.c._raw_bytes, self.c._conversion_bytes),
            (0, 0, 0),
        )

    async def test_real_lifecycle_disable_and_epoch_reject_old_context(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            await self.f.lifecycle.stop("sample/demo", timeout=1)
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()
            identity, _ = await self.f.lifecycle.start_candidate(
                "sample/demo", "png-restart"
            )
            await self.f.health.prepare(
                "sample/demo", self.f.registry.snapshot().module("sample/demo").manifest
            )
            self.f.lifecycle.publish_committed_intent(
                "sample/demo",
                "png-restart",
                identity,
                True,
                self.f.registry.snapshot().revision,
            )
            with self.assertRaises(GeneratedPngError):
                await self.c.revalidate(lease)

    async def test_deadline_after_authorization_await_denies_bind_and_no_reservation(
        self,
    ):
        view = self.f.new_view(deadline=self.f.now + 1)
        self.f.routes.arm()
        task = asyncio.create_task(self.c.bind(view, self.f.result))
        await self.f.routes.entered.wait()
        self.f.now += 1
        self.f.routes.resume.set()
        with self.assertRaises(GeneratedPngError):
            await task
        self.assertEqual((len(self.c._entries), self.c._raw_bytes), (0, 0))

    async def test_grant_utc_expiry_during_final_route_await_denies(self):
        grant = self.f.grant()
        view = self.f.new_view(grant_id=grant.grant_id, grant_revision=1)
        self.f.grants.arm()
        task = asyncio.create_task(
            self.c.bind(view, self.f.new_result(Privacy.PRIVATE))
        )
        await self.f.grants.entered.wait()
        self.f.utc = grant.expires_at
        self.f.grants.resume.set()
        with self.assertRaises(GeneratedPngError):
            await task
        self.assertFalse(self.c._entries)

    @staticmethod
    async def settle_tasks(*tasks):
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_public_revalidation_independent_task_queues_and_owns_gate(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            start = asyncio.Event()
            started = asyncio.Event()
            writer_start = asyncio.Event()
            changed = asyncio.Event()
            order = []

            async def revalidate():
                await start.wait()
                started.set()
                await self.c.revalidate(lease)
                order.append("revalidate")

            async def mutation():
                await writer_start.wait()
                async with self.f.admission.mutation("test:queued-after-revalidation"):
                    order.append("mutation")
                    changed.set()

            # Both tasks have their own clean execution contexts, created before
            # the parent enters control. Do not erase an inherited control token.
            child = asyncio.create_task(revalidate())
            writer = asyncio.create_task(mutation())
            self.f.routes.arm()
            try:
                async with self.f.admission.mutation("test:parent-holder"):
                    parent_token = self.f.admission._active_mutation_token
                    start.set()
                    await asyncio.wait_for(started.wait(), timeout=2)
                    await asyncio.sleep(0)
                    self.assertFalse(self.f.routes.entered.is_set())
                    self.assertFalse(child.done())
                    self.assertIs(self.f.admission._active_mutation_token, parent_token)
                await asyncio.wait_for(self.f.routes.entered.wait(), timeout=2)
                self.assertIsNotNone(self.f.admission._active_mutation_token)
                self.assertIsNot(self.f.admission._active_mutation_token, parent_token)
                writer_start.set()
                await asyncio.sleep(0)
                self.assertFalse(changed.is_set())
                self.f.routes.resume.set()
                await asyncio.wait_for(child, timeout=2)
                await asyncio.wait_for(writer, timeout=2)
                self.assertEqual(order, ["revalidate", "mutation"])
                self.assertIsNone(self.f.admission._active_mutation_token)
            finally:
                await self.settle_tasks(child, writer)

    async def test_inherited_child_revalidation_fails_closed_and_no_future_gate_entry(
        self,
    ):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            self.assertFalse(hasattr(self.c, "_revalidate_in_output_control"))
            async with self.f.admission.mutation("test:parent-inherited-control"):
                with self.assertRaises(MutationReentryError):
                    await self.c.revalidate(lease)
                child = asyncio.create_task(self.c.revalidate(lease))
                try:
                    with self.assertRaises(MutationReentryError):
                        await child
                    self.assertEqual(lease._operations, 0)
                finally:
                    await self.settle_tasks(child)

    async def test_queued_public_revalidation_cancel_close_preserves_real_pin(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            start = asyncio.Event()
            started = asyncio.Event()

            async def revalidate():
                await start.wait()
                started.set()
                await self.c.revalidate(lease)

            child = asyncio.create_task(revalidate())
            try:
                async with self.f.admission.mutation("test:cancel-queued-holder"):
                    start.set()
                    await asyncio.wait_for(started.wait(), timeout=2)
                    await asyncio.sleep(0)
                    self.assertFalse(child.done())
                    self.assertEqual(lease._operations, 1)
                    self.assertFalse(self.c.close())
                    child.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await child
                    self.assertEqual(lease._operations, 0)
                    self.assertFalse(await self.c.wait_closed(0))
                    self.assertEqual(self.c._raw_bytes, len(self.bitmap.content))
                    self.assertGreater(self.c._conversion_bytes, 0)
                    lease.release()
                    lease.release()
            finally:
                await self.settle_tasks(child)
        self.assertTrue(await self.c.wait_closed(0))
        self.assertEqual(
            (len(self.c._entries), self.c._raw_bytes, self.c._conversion_bytes),
            (0, 0, 0),
        )

    async def test_inflight_public_revalidation_cancel_close_drains_after_unwind(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            self.f.routes.arm()
            child = asyncio.create_task(self.c.revalidate(lease))
            try:
                await asyncio.wait_for(self.f.routes.entered.wait(), timeout=2)
                self.assertEqual(lease._operations, 1)
                lease.release()
                self.assertFalse(self.c.close())
                self.assertEqual(self.c._raw_bytes, len(self.bitmap.content))
                self.assertGreater(self.c._conversion_bytes, 0)
                child.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await child
                self.assertEqual(lease._operations, 0)
                self.assertTrue(await self.c.wait_closed(0))
                self.assertEqual(
                    (len(self.c._entries), self.c._raw_bytes, self.c._conversion_bytes),
                    (0, 0, 0),
                )
                async with self.f.admission.mutation("test:after-cancel"):
                    self.assertIsNotNone(self.f.admission._active_mutation_token)
            finally:
                await self.settle_tasks(child)

    async def test_mutation_gate_serializes_writes_while_public_read_awaits(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            self.f.routes.arm()
            read = asyncio.create_task(lease.read_once())
            await self.f.routes.entered.wait()
            changed = asyncio.Event()

            async def mutation():
                async with self.f.admission.mutation("test:queued-write"):
                    changed.set()

            writer = asyncio.create_task(mutation())
            await asyncio.sleep(0)
            self.assertFalse(changed.is_set())
            self.f.routes.resume.set()
            self.assertEqual(await read, self.bitmap.content)
            await writer
            self.assertTrue(changed.is_set())

    async def test_count_raw_conversion_exact_caps_plus_one_and_double_operations(self):
        size = len(self.bitmap.content)
        conversion = 3 * 4 * ((size + 2) // 3)
        c = self.new_coordinator(
            max_contexts=2, max_raw_bytes=size, max_conversion_bytes=conversion
        )
        context, reference = await self.registered(c)
        second = await c.bind(self.f.view, self.f.result)
        with self.assertRaises(GeneratedPngError):
            await c.bind(self.f.view, self.f.result)
        with self.assertRaises(GeneratedPngError):
            await c.register(second, self.bitmap)
        with self.assertRaises(GeneratedPngError):
            await c.register(context, self.bitmap)
        async with self.pin(context, reference, coordinator=c):
            self.assertEqual(c._conversion_bytes, conversion)
            with self.assertRaises(GeneratedPngError):
                async with self.pin(context, reference, coordinator=c):
                    self.fail("double pin accepted")
        with self.assertRaises(GeneratedPngError):
            async with self.pin(context, reference, coordinator=c):
                self.fail("consumed context pinned again")
        smaller = self.new_coordinator(max_conversion_bytes=conversion - 1)
        small_context, small_ref = await self.registered(smaller)
        with self.assertRaises(GeneratedPngError):
            async with self.pin(small_context, small_ref, coordinator=smaller):
                self.fail("over conversion budget")
        c.release(context)
        await c.register(second, self.bitmap)

    async def test_concurrent_registration_reserves_raw_before_gate_wait(self):
        c = self.new_coordinator(max_raw_bytes=len(self.bitmap.content))
        first = await c.bind(self.f.view, self.f.result)
        second = await c.bind(self.f.view, self.f.result)
        self.f.routes.arm()
        task = asyncio.create_task(c.register(first, self.bitmap))
        await self.f.routes.entered.wait()
        self.assertEqual(c._raw_bytes, len(self.bitmap.content))
        with self.assertRaises(GeneratedPngError):
            await c.register(second, self.bitmap)
        with self.assertRaises(GeneratedPngError):
            await c.register(first, self.bitmap)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(c._raw_bytes, 0)
        await c.register(second, self.bitmap)

    async def test_count_reservation_during_bind_await_cannot_be_oversold(self):
        c = self.new_coordinator(max_contexts=1)
        self.f.identity_reads.arm()
        task = asyncio.create_task(c.bind(self.f.view, self.f.result))
        await self.f.identity_reads.entered.wait()
        with self.assertRaises(GeneratedPngError):
            await c.bind(self.f.view, self.f.result)
        self.assertFalse(c.close())
        self.assertEqual(len(c._entries), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(await c.wait_closed(0))
        self.assertFalse(c._entries)

    async def test_ttl_boundary_and_timer_retire_without_followup_request(self):
        c = self.new_coordinator(ttl=0.01)
        context, reference = await self.registered(c)
        expiry = c._entries[context].expires
        self.f.now = expiry - 0.0001
        c.expire()
        self.assertEqual(len(c._entries), 1)
        self.f.now = expiry
        await asyncio.sleep(0.03)
        self.assertFalse(c._entries)
        self.assertIsNone(c._timer)
        self.assertEqual(c._raw_bytes, 0)
        with self.assertRaises(GeneratedPngError):
            async with self.pin(context, reference, coordinator=c):
                self.fail("expired reference")

    async def test_ttl_retired_pin_still_blocks_count_raw_and_conversion_until_release(
        self,
    ):
        c = self.new_coordinator(max_contexts=1)
        context, reference = await self.registered(c)
        async with self.pin(context, reference, coordinator=c) as lease:
            self.f.now += 30
            c.expire()
            self.assertEqual(
                (len(c._entries), c._raw_bytes), (1, len(self.bitmap.content))
            )
            with self.assertRaises(GeneratedPngError):
                await c.bind(self.f.view, self.f.result)
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()
        self.assertEqual(
            (len(c._entries), c._raw_bytes, c._conversion_bytes), (0, 0, 0)
        )

    async def test_close_with_pin_timeout_cancel_and_idempotent_real_release(self):
        context, reference = await self.registered()
        observer_count = len(self.f.issuer._release_observers)
        async with self.pin(context, reference) as lease:
            self.assertFalse(self.c.close())
            self.assertFalse(self.c.close())
            self.assertIsNone(self.c._timer)
            self.assertEqual(len(self.f.issuer._release_observers), observer_count - 1)
            self.assertFalse(await self.c.wait_closed(0))
            waiter = asyncio.create_task(self.c.wait_closed(1))
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()
            self.assertGreater(self.c._raw_bytes, 0)
            lease.release()
            lease.release()
        self.assertTrue(await self.c.wait_closed(0))
        self.assertEqual((self.c._raw_bytes, self.c._conversion_bytes), (0, 0))

    async def test_close_pending_register_and_pin_auth_await_does_not_late_commit(self):
        for stage in ("register", "pin"):
            with self.subTest(stage=stage):
                c = self.new_coordinator()
                context = await c.bind(self.f.view, self.f.result)
                reference = (
                    await c.register(context, self.bitmap) if stage == "pin" else None
                )
                self.f.routes.arm()
                operation = (
                    c.register(context, self.bitmap)
                    if stage == "register"
                    else self.pin(context, reference, coordinator=c).__aenter__()
                )
                task = asyncio.create_task(operation)
                await self.f.routes.entered.wait()
                self.assertFalse(c.close())
                self.assertEqual(c._raw_bytes, len(self.bitmap.content))
                self.f.routes.resume.set()
                with self.assertRaises(GeneratedPngError):
                    await task
                self.assertTrue(await c.wait_closed(0))
                self.assertEqual((c._raw_bytes, c._conversion_bytes), (0, 0))

    async def test_release_pin_while_read_awaiting_retains_reservations_until_unwind(
        self,
    ):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            self.f.routes.arm()
            reader = asyncio.create_task(lease.read_once())
            await self.f.routes.entered.wait()
            lease.release()
            self.assertFalse(self.c.close())
            self.assertEqual(self.c._raw_bytes, len(self.bitmap.content))
            self.assertGreater(self.c._conversion_bytes, 0)
            self.f.routes.resume.set()
            with self.assertRaises(GeneratedPngError):
                await reader
            self.assertTrue(await self.c.wait_closed(0))
            self.assertEqual((self.c._raw_bytes, self.c._conversion_bytes), (0, 0))

    async def test_cancel_bind_register_pin_read_and_revalidate_awaits(self):
        for stage in ("bind", "register", "pin", "read", "revalidate"):
            with self.subTest(stage=stage):
                c = self.new_coordinator()
                context = reference = manager = lease = None
                if stage != "bind":
                    context = await c.bind(self.f.view, self.f.result)
                if stage in ("pin", "read", "revalidate"):
                    reference = await c.register(context, self.bitmap)
                if stage in ("read", "revalidate"):
                    manager = self.pin(context, reference, coordinator=c)
                    lease = await manager.__aenter__()
                self.f.routes.arm()
                if stage == "bind":
                    operation = c.bind(self.f.view, self.f.result)
                elif stage == "register":
                    operation = c.register(context, self.bitmap)
                elif stage == "pin":
                    operation = self.pin(context, reference, coordinator=c).__aenter__()
                elif stage == "read":
                    operation = lease.read_once()
                else:
                    operation = c.revalidate(lease)
                task = asyncio.create_task(operation)
                await self.f.routes.entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                if manager is not None:
                    await manager.__aexit__(None, None, None)
                self.assertTrue(c.close())
                self.assertEqual(
                    (len(c._entries), c._raw_bytes, c._conversion_bytes), (0, 0, 0)
                )

    async def test_issuer_release_and_close_during_bind_identity_await(self):
        self.f.identity_reads.arm()
        task = asyncio.create_task(self.c.bind(self.f.view, self.f.result))
        await self.f.identity_reads.entered.wait()
        self.f.issuer.release(self.f.view)
        self.assertFalse(self.c.close())
        self.assertEqual(len(self.c._entries), 1)
        self.f.identity_reads.resume.set()
        with self.assertRaises(GeneratedPngError):
            await task
        self.assertTrue(await self.c.wait_closed(0))

    async def test_decoder_error_or_cancellation_drops_raw_reservation_and_safe_repr(
        self,
    ):
        context = await self.c.bind(self.f.view, self.f.result)
        with self.assertRaises(GeneratedPngError) as caught:
            await self.c.register(
                context, replace_idat(self.bitmap, b"decoder-SENSITIVE")
            )
        self.assertNotIn("SENSITIVE", str(caught.exception))
        self.assertEqual(self.c._raw_bytes, 0)
        with patch(
            "ygl_test_subject.services.generated_png.validate_png",
            side_effect=asyncio.CancelledError,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.c.register(context, self.bitmap)
        self.assertFalse(self.c._decoding)
        self.assertEqual(self.c._raw_bytes, 0)
        reference = await self.c.register(context, self.bitmap)
        async with self.pin(context, reference) as lease:
            for value in (self.c, context, self.c._entries[context], reference, lease):
                self.assertNotIn("SENSITIVE", repr(value))
                self.assertNotIn(repr(self.bitmap.content), repr(value))

    async def test_release_and_ttl_at_each_pending_authority_stage_never_yield_bytes(
        self,
    ):
        for retire in ("issuer", "ttl"):
            for stage in ("register", "pin", "read", "revalidate"):
                with self.subTest(retire=retire, stage=stage):
                    c = self.new_coordinator()
                    view = self.f.new_view()
                    context = await c.bind(view, self.f.result)
                    reference = (
                        await c.register(context, self.bitmap)
                        if stage != "register"
                        else None
                    )
                    manager = None
                    if stage in ("read", "revalidate"):
                        manager = self.pin(
                            context, reference, coordinator=c, invocation=view
                        )
                        lease = await manager.__aenter__()
                    self.f.routes.arm()
                    if stage == "register":
                        operation = c.register(context, self.bitmap)
                    elif stage == "pin":
                        operation = self.pin(
                            context, reference, coordinator=c, invocation=view
                        ).__aenter__()
                    elif stage == "read":
                        operation = lease.read_once()
                    else:
                        operation = c.revalidate(lease)
                    task = asyncio.create_task(operation)
                    await self.f.routes.entered.wait()
                    if retire == "issuer":
                        self.f.issuer.release(view)
                    else:
                        self.f.now = c._entries[context].expires
                        c.expire()
                    self.assertEqual(
                        (len(c._entries), c._raw_bytes), (1, len(self.bitmap.content))
                    )
                    self.f.routes.resume.set()
                    with self.assertRaises(GeneratedPngError):
                        await task
                    if manager is not None:
                        await manager.__aexit__(None, None, None)
                    self.assertTrue(c.close())
                    self.assertEqual((c._raw_bytes, c._conversion_bytes), (0, 0))

    async def test_real_lifecycle_disable_during_await_blocks_late_bind(self):
        self.f.routes.arm()
        task = asyncio.create_task(self.c.bind(self.f.view, self.f.result))
        await self.f.routes.entered.wait()
        await self.f.lifecycle.stop("sample/demo", timeout=1)
        self.f.routes.resume.set()
        with self.assertRaises(GeneratedPngError):
            await task
        self.assertFalse(self.c._entries)

    async def test_wrong_loop_and_thread_cannot_mutate_registry(self):
        context, _ = await self.registered()

        async def another_loop():
            with self.assertRaises(GeneratedPngError):
                self.c.release(context)

        await asyncio.to_thread(lambda: asyncio.run(another_loop()))

        def no_loop():
            with self.assertRaises(GeneratedPngError):
                self.c.close()

        await asyncio.to_thread(no_loop)
        self.assertEqual(len(self.c._entries), 1)

    async def test_real_health_revision_change_revokes_existing_lease(self):
        context, reference = await self.registered()
        async with self.pin(context, reference) as lease:
            async with self.f.admission.mutation("test:health-config"):
                self.f.health.publish_config(
                    "sample/demo",
                    ConfigSnapshot(
                        2,
                        {"ready": None},
                        target=ConfigTarget("png-tests", "sample/demo"),
                    ),
                    changed_fields=frozenset({"ready"}),
                )
            with self.assertRaises(GeneratedPngError):
                await lease.read_once()
            with self.assertRaises(GeneratedPngError):
                await self.c.revalidate(lease)

    async def test_restart_rejects_old_identity_and_preserves_existing_durable_claim(
        self,
    ):
        claim = await self.f.root_outputs.claim(
            self.f.view.invocation_id,
            "stable-output",
            "a" * 64,
            "owner-token",
            now=self.f.utc,
            lease_expires_at=self.f.utc + timedelta(seconds=60),
        )
        context, reference = await self.registered()
        self.assertTrue(self.c.close())
        new = self.new_coordinator()
        with self.assertRaises(GeneratedPngError):
            await new.register(context, self.bitmap)
        with self.assertRaises(GeneratedPngError):
            async with self.pin(context, reference, coordinator=new):
                self.fail("old identity accepted")
        same = await self.f.root_outputs.claim(
            self.f.view.invocation_id,
            "stable-output",
            "a" * 64,
            "owner-token",
            now=self.f.utc,
            lease_expires_at=self.f.utc + timedelta(seconds=60),
        )
        self.assertEqual(same, claim)

    async def test_constructor_limits_can_only_lower_and_real_output_required(self):
        for field, value in (
            ("max_contexts", 9),
            ("max_raw_bytes", 4_194_305),
            ("max_conversion_bytes", 16_777_217),
            ("max_png_bytes", 1_048_577),
            ("max_width", 2049),
            ("max_height", 8193),
            ("max_pixels", 8_000_001),
            ("ttl", 61),
            ("ttl", float("nan")),
            ("max_contexts", True),
        ):
            with self.assertRaises(ValueError):
                self.f.new_coordinator(replace(GeneratedPngLimits(), **{field: value}))
        with self.assertRaises(TypeError):
            GeneratedPngCoordinator(output_authority=lambda: True)
