"""Current actor reads cross only explicitly declared read-only edges."""

import asyncio
import unittest
from dataclasses import replace

from ygl_test_subject.services.dependency_calls import DependencyInvoker
from ygl_test_subject.services.owner_authority import OwnerRouteProofAuthority

import tests.core.test_dependency_invoker as fixtures
import yomihime_game_link_sdk as ygl


class _Principals:
    async def principal_id(self, view):
        return "principal:" + view.actor_id


class _Routes:
    async def resolve(self, view):
        raise AssertionError("dependency reads must not acquire output authority")


class OwnerDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def ready(self, *, declared=True, origin=ygl.InvocationOrigin.COMMAND,
                    effect=ygl.CapabilityEffect.READ_ONLY):
        fixture = fixtures.DependencyInvokerTests()
        fixture.setUp()
        reference = ygl.CapabilityReference("dependency-tests/target", "target.query")
        target = fixtures._capability("target.query", policy=ygl.InvocationPolicy.COMMAND_ONLY,
                                     privacy=ygl.PrivacyFloor.OWNER)
        target = replace(target, effect=effect)
        authority = OwnerRouteProofAuthority(fixture.issuer, fixture.lifecycle.admission,
                                            _Principals(), _Routes())
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        test = self

        class Handler:
            calls = 0
            child = None

            async def invoke(self, child, parameters):
                self.calls += 1
                self.child = child
                principal = await authority.require_current(child)
                test.entered.set()
                if getattr(test, "block", False):
                    await test.release.wait()
                return ygl.CapabilityResult("owner-read", ygl.ResultStatus.SUCCESS,
                    document=ygl.DisplayDocument("Identity", "private",
                        (ygl.FieldsBlock({"identity": principal}),),
                        privacy=ygl.Privacy.PRIVATE), privacy=ygl.Privacy.PRIVATE)

        handler = Handler()
        old, _, _ = await fixture._ready(
            (fixtures._capability("source.query", required=(reference,) if declared else ()),),
            target, target_handler=handler)
        fixture.issuer.release(old)
        snapshot = fixture.registry.snapshot()
        source = snapshot.module("dependency-tests/source")
        parent = fixture.issuer.issue(origin=origin, module_id=source.module_id,
            module_epoch=source.epoch, registry_revision=snapshot.revision,
            actor_id="adapter-a:alice", adapter_id="adapter-a", conversation_id="group-a",
            capability_id="source.query")
        fixture.lifecycle.admission.admit(parent, "source.query")
        caller = fixture.caller_issuer.issue(parent, "source.query")
        invoker = DependencyInvoker(fixture.registry, fixture.issuer, fixture.lifecycle,
            fixture.caller_issuer, caller, owner_authority=authority, clock=fixture.clock)
        return fixture, authority, parent, reference, handler, invoker

    async def test_group_actor_reads_private_data_without_output_or_grant(self):
        fixture, authority, parent, reference, handler, invoker = await self.ready()
        result = await invoker.invoke(parent, reference, {})
        self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
        self.assertIs(result.privacy, ygl.Privacy.PRIVATE)
        self.assertEqual(result.document.ordered_blocks[0].fields["identity"], "principal:adapter-a:alice")
        self.assertIsNone(result.model_facts)
        self.assertIsNone(handler.child.grant_id)
        with self.assertRaises(PermissionError):
            await authority.require_current(handler.child)
        self.assertNotIn(handler.child.invocation_id, fixture.issuer._issued)

    async def test_undeclared_owner_edge_never_calls_provider(self):
        _, _, parent, reference, handler, invoker = await self.ready(declared=False)
        result = await invoker.invoke(parent, reference, {})
        self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertEqual(handler.calls, 0)

    async def test_tool_and_writable_owner_targets_are_rejected(self):
        for options in ({"origin": ygl.InvocationOrigin.LLM_TOOL},
                        {"effect": ygl.CapabilityEffect.WRITE}):
            with self.subTest(options=options):
                _, _, parent, reference, handler, invoker = await self.ready(**options)
                result = await invoker.invoke(parent, reference, {})
                self.assertIs(result.status, ygl.ResultStatus.ERROR)
                self.assertEqual(handler.calls, 0)

    async def test_parent_release_while_provider_awaits_discards_result(self):
        self.block = True
        fixture, _, parent, reference, _, invoker = await self.ready()
        task = asyncio.create_task(invoker.invoke(parent, reference, {}))
        await asyncio.wait_for(self.entered.wait(), 1)
        fixture.issuer.release(parent)
        self.release.set()
        result = await task
        self.assertIs(result.status, ygl.ResultStatus.ERROR)

    async def test_copy_is_not_authority(self):
        self.block = True
        _, authority, parent, reference, handler, invoker = await self.ready()
        task = asyncio.create_task(invoker.invoke(parent, reference, {}))
        await asyncio.wait_for(self.entered.wait(), 1)
        with self.assertRaises(PermissionError):
            await authority.require_current(replace(handler.child))
        self.release.set()
        self.assertIs((await task).status, ygl.ResultStatus.SUCCESS)
