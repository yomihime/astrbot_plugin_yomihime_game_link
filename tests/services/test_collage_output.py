"""Focused offline private collage preparation and exact-event dispatch gates."""

import asyncio
import base64
import io
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from ygl_test_subject.adapters.astrbot.message_port import AstrBotMessagePort
from ygl_test_subject.core.contracts.services import GrantStatus
from ygl_test_subject.core.ports import MessageReceipt, MessageStatus
from ygl_test_subject.presentation.card_bitmap import RenderedBitmap
from ygl_test_subject.presentation.command_card import (
    TOO_LARGE,
    CommandCardRenderer,
    CommandPresentation,
)
from ygl_test_subject.presentation.png_validation import (
    PngValidationError,
    PngValidationLimits,
    validate_png,
)
from ygl_test_subject.services.collage_cache import (
    load_finished_pages,
    store_finished_pages,
)
from ygl_test_subject.services.generated_png import (
    GeneratedPngCoordinator,
    GeneratedPngLimits,
)
from ygl_test_subject.services.output import (
    LifecycleApprovedSendScheduler,
    OutputStatus,
)
from ygl_test_subject.services.prepared_command import PreparedCommand

from tests.fixtures.b03_runtime import build_runtime
from tests.fixtures.generated_png import GeneratedPngFixture
from yomihime_game_link_sdk.cards import (
    Asset,
    CollageItem,
    Document,
    ResourceImage,
    WeightedCollage,
)
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import DisplayAudience, TextBlock
from yomihime_game_link_sdk.errors import AccessDenied, CacheUnavailable
from yomihime_game_link_sdk.services import ResourceReference
from yomihime_game_link_sdk.storage import (
    CacheEntry,
    CacheLookup,
    CacheLookupStatus,
    CacheVisibility,
    OwnerScope,
    ResourceMetadata,
)


def _png(color):
    image = Image.new("RGB", (12, 12), color)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return RenderedBitmap(stream.getvalue(), 12, 12)


class _Event:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.gate = None

    def chain_result(self, components):
        return tuple(components)

    async def send(self, chain):
        self.calls.append(chain)
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        return True


class _Scope:
    def __init__(self):
        self.cache = self
        self.resources = self
        self.stores = 0

    async def lookup(self, _query):
        return CacheLookup(CacheLookupStatus.MISS)

    async def store(self, *_args, **_kwargs):
        self.stores += 1
        raise CacheUnavailable()


class _CachingScope:
    def __init__(self):
        self.cache = self
        self.resources = self
        self.manifests = {}
        self.contents = {}
        self.stores = 0

    async def lookup(self, query):
        assert query.visibility is CacheVisibility.USER
        payload = self.manifests.get(query.key)
        if payload is None:
            return CacheLookup(CacheLookupStatus.MISS)
        entry = CacheEntry(
            query.key,
            payload,
            datetime.now(UTC) + timedelta(hours=1),
        )
        return CacheLookup(CacheLookupStatus.HIT, entry)

    async def put(self, key, payload, *, visibility, ttl_seconds):
        assert visibility is CacheVisibility.USER
        assert ttl_seconds > 0
        self.manifests[key] = payload

    async def store(self, media_type, content, *, visibility, ttl_seconds):
        assert media_type == "image/png"
        assert visibility is CacheVisibility.USER
        assert ttl_seconds > 0
        self.stores += 1
        asset_id = f"stored-page-{self.stores}"
        self.contents[asset_id] = content
        return ResourceReference(asset_id, media_type, OwnerScope.user("current-user"))

    async def read(self, asset_id, *, visibility):
        assert visibility is CacheVisibility.USER
        return self.contents[asset_id]


class _ResourceScope(_Scope):
    def __init__(self, content):
        super().__init__()
        self.content = content
        self.reads = []

    async def metadata(self, asset_id, *, visibility):
        self.reads.append(("metadata", asset_id, visibility))
        if self.content is None:
            return None
        return ResourceMetadata(
            asset_id,
            "image/png",
            OwnerScope.public(),
            len(self.content),
        )

    async def read(self, asset_id, *, visibility):
        self.reads.append(("read", asset_id, visibility))
        assert visibility is CacheVisibility.PUBLIC
        return self.content


class _CancelBeforeRunScheduler(LifecycleApprovedSendScheduler):
    def schedule(self, permit, task, *, name):
        super().schedule(permit, task, name=name)
        owned = self._lifecycle.scope(permit.module_id).tasks[-1]
        owned.cancel()


class PrivateCollageOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = await GeneratedPngFixture().start(owner=True)
        self.event = _Event()
        self.scope = _Scope()
        self.bitmaps = (_png("#132246"), _png("#451933"))
        self.render_calls = 0
        self.last_rendered_card = None
        self.text_sends = []

        class TextPort:
            async def send(_, target, payload):
                self.text_sends.append((target, payload))
                return MessageReceipt(MessageStatus.ACCEPTED)

        self.fixture.output._message_port = TextPort()
        self.fixture.output._command_card_renderer = self.render
        self.fixture.output._collage_scope_binder = lambda _invocation: self.bind()
        self.fixture.output._command_preparer = self.prepare
        self.ingress = SimpleNamespace(
            adapter_id=self.fixture.view.adapter_id,
            delivery_route=self.fixture.route.delivery_route,
            conversation_id=self.fixture.view.conversation_id,
            conversation_kind=self.fixture.route.kind,
            actor_id=self.fixture.view.actor_id,
            evidence=SimpleNamespace(event=self.event),
        )
        self.port = AstrBotMessagePort(
            object(),
            image_factory=lambda data: SimpleNamespace(
                file="base64://" + base64.b64encode(data).decode("ascii")
            ),
            command_current=lambda ingress: ingress is self.ingress,
        )
        card = Document(
            "Generic poster",
            blocks=(
                WeightedCollage(
                    (
                        CollageItem("one", 2, Asset("png", self.bitmaps[0].content)),
                        CollageItem("two", 1, Asset("png", self.bitmaps[1].content)),
                    )
                ),
            ),
        )
        self.result = replace(
            self.fixture.result,
            document=replace(self.fixture.result.document, card=card),
        )

    async def asyncTearDown(self):
        await self.fixture.stop()

    async def bind(self):
        return self.scope

    def render(self, document, *, image=True, **_kwargs):
        if image:
            self.render_calls += 1
            self.last_rendered_card = document.card
            return CommandPresentation(
                "complete private ranked text", self.bitmaps[0], self.bitmaps
            )
        return CommandPresentation("complete private ranked text")

    async def prepare(self, invocation, result, target, payload, *, image_current):
        prepared = self.port.prepare_command(
            self.ingress, target, payload, image_current=image_current
        )
        return PreparedCommand(
            invocation,
            result,
            target,
            prepared.check_current,
            prepared.dispatch,
        )

    async def test_owner_private_two_pages_one_exact_event_send(self):
        outcome = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(outcome.status, OutputStatus.SENT, outcome)
        self.assertEqual(self.render_calls, 1)
        self.assertEqual(self.text_sends, [])
        self.assertEqual(len(self.event.calls), 1)
        chain = self.event.calls[0]
        self.assertEqual(len(chain), 2)
        self.assertEqual(
            [base64.b64decode(part.file.removeprefix("base64://")) for part in chain],
            [page.content for page in self.bitmaps],
        )
        for _ in range(20):
            if not self.fixture.output._collage_png._entries:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.fixture.output._collage_png._entries, {})

    async def test_page_two_preparation_failure_keeps_complete_private_text(self):
        calls = 0

        def fail_second(data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("image conversion unavailable")
            return SimpleNamespace(file=base64.b64encode(data).decode("ascii"))

        self.port._image_factory = fail_second
        outcome = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(outcome.status, OutputStatus.SENT, outcome)
        self.assertEqual(self.event.calls, [])
        self.assertEqual(len(self.text_sends), 1)
        self.assertEqual(self.text_sends[0][1].text, "complete private ranked text")
        self.assertEqual(self.fixture.output._collage_png._entries, {})

    async def test_timeout_retains_pins_until_single_send_completes(self):
        self.event.gate = asyncio.Event()
        self.fixture.output._send_timeout = 0.01
        outcome = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(outcome.status, OutputStatus.UNKNOWN, outcome)
        await self.event.entered.wait()
        self.assertEqual(len(self.event.calls), 1)
        self.assertTrue(self.fixture.output._collage_png._entries)
        self.event.gate.set()
        for _ in range(20):
            if not self.fixture.output._collage_png._entries:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.fixture.output._collage_png._entries, {})
        self.assertEqual(len(self.event.calls), 1)

    async def test_user_finished_pages_hit_without_second_render(self):
        self.scope = _CachingScope()
        first = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(first.status, OutputStatus.SENT, first)
        self.assertEqual(self.scope.stores, 2)
        view = self.fixture.new_view()
        await self.fixture.owner.capture(view, self.fixture.issuer.lease_for(view))
        self.fixture.view = view
        self.event = _Event()
        self.ingress.evidence.event = self.event
        second = await self.fixture.output.route(view, self.result)
        self.assertIs(second.status, OutputStatus.SENT, second)
        self.assertEqual(self.render_calls, 1)
        self.assertEqual(self.scope.stores, 2)
        self.assertEqual(len(self.event.calls), 1)

    async def test_granted_private_pixels_never_enter_user_finished_cache(self):
        self.scope = _CachingScope()
        granted = await GeneratedPngFixture().start(owner=False)
        try:
            granted.output._message_port = self.fixture.output._message_port
            granted.output._command_card_renderer = self.render
            granted.output._collage_scope_binder = lambda _view: self.bind()
            granted.output._command_preparer = self.prepare
            granted.output._collage_png = GeneratedPngCoordinator(
                output_authority=granted.output,
                limits=GeneratedPngLimits.collage(),
                collage_profile=True,
                monotonic_clock=lambda: granted.now,
                utc_clock=lambda: granted.utc,
            )
            granted.coordinators.append(granted.output._collage_png)
            grant = granted.grant()
            view = granted.new_view(
                grant_id=grant.grant_id, grant_revision=grant.revision
            )
            outcome = await granted.output.route(view, self.result)
            self.assertIs(outcome.status, OutputStatus.SENT, outcome)
            self.assertEqual(self.scope.stores, 0)
            self.assertEqual(self.scope.manifests, {})
            async with granted.admission.mutation("revoke-grant-before-reuse"):
                granted.grants.value = replace(grant, status=GrantStatus.REVOKED)
            later = granted.new_view(
                grant_id=grant.grant_id, grant_revision=grant.revision
            )
            rejected = await granted.output.route(later, self.result)
            self.assertIsNot(rejected.status, OutputStatus.SENT)
            self.assertEqual(len(self.event.calls), 1)
        finally:
            await granted.stop()

    async def test_layout_algorithm_change_invalidates_finished_pages(self):
        self.scope = _CachingScope()
        first = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(first.status, OutputStatus.SENT, first)
        view = self.fixture.new_view()
        await self.fixture.owner.capture(view, self.fixture.issuer.lease_for(view))
        self.fixture.view = view
        self.event = _Event()
        self.ingress.evidence.event = self.event
        with patch(
            "ygl_test_subject.presentation.layout_collage.LAYOUT_VERSION",
            "synthetic-next-layout",
        ):
            second = await self.fixture.output.route(view, self.result)
        self.assertIs(second.status, OutputStatus.SENT, second)
        self.assertEqual(self.render_calls, 2)
        self.assertEqual(self.scope.stores, 4)
        self.assertEqual(len(self.event.calls), 1)

    async def test_damaged_finished_page_is_rendered_again(self):
        self.scope = _CachingScope()
        first = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(first.status, OutputStatus.SENT, first)
        asset_id = next(iter(self.scope.contents))
        self.scope.contents[asset_id] = b"damaged-png"
        view = self.fixture.new_view()
        await self.fixture.owner.capture(view, self.fixture.issuer.lease_for(view))
        self.fixture.view = view
        self.event = _Event()
        self.ingress.evidence.event = self.event
        second = await self.fixture.output.route(view, self.result)
        self.assertIs(second.status, OutputStatus.SENT, second)
        self.assertEqual(self.render_calls, 2)
        self.assertEqual(self.scope.stores, 4)
        self.assertEqual(len(self.event.calls), 1)

    async def test_owner_route_changes_after_chain_preparation_send_nothing(self):
        original = self.prepare

        async def stale_after_prepare(*args, **kwargs):
            prepared = await original(*args, **kwargs)
            async with self.fixture.admission.mutation("change-owner-route"):
                self.fixture.routes.value = replace(
                    self.fixture.route, delivery_route="changed-direct-route"
                )
            return prepared

        self.fixture.output._command_preparer = stale_after_prepare
        outcome = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(outcome.status, OutputStatus.FAILED, outcome)
        self.assertEqual(self.event.calls, [])
        self.assertEqual(self.text_sends, [])
        self.assertEqual(self.fixture.output._collage_png._entries, {})

    async def test_private_poster_reads_public_cover_through_declared_scope(self):
        self.scope = _ResourceScope(self.bitmaps[0].content)
        collage = self.result.document.card.blocks[0]
        first = replace(
            collage.items[0],
            image=ResourceImage("public-cover", CacheVisibility.PUBLIC, "cover", True),
        )
        card = replace(
            self.result.document.card,
            blocks=(replace(collage, items=(first, collage.items[1])),),
        )
        result = replace(self.result, document=replace(self.result.document, card=card))
        outcome = await self.fixture.output.route(self.fixture.view, result)
        self.assertIs(outcome.status, OutputStatus.SENT, outcome)
        self.assertEqual(
            self.scope.reads,
            [
                ("metadata", "public-cover", CacheVisibility.PUBLIC),
                ("read", "public-cover", CacheVisibility.PUBLIC),
            ],
        )
        selected = self.last_rendered_card.blocks[0].items[0].image
        self.assertIs(type(selected), Asset)
        self.assertEqual(selected.content, self.bitmaps[0].content)
        self.assertEqual(len(self.event.calls), 1)

    async def test_missing_required_cover_sends_complete_private_text(self):
        self.scope = _ResourceScope(None)
        collage = self.result.document.card.blocks[0]
        first = replace(
            collage.items[0],
            image=ResourceImage("absent-cover", CacheVisibility.PUBLIC, "cover", True),
        )
        card = replace(
            self.result.document.card,
            blocks=(replace(collage, items=(first, collage.items[1])),),
        )
        result = replace(self.result, document=replace(self.result.document, card=card))
        outcome = await self.fixture.output.route(self.fixture.view, result)
        self.assertIs(outcome.status, OutputStatus.SENT, outcome)
        self.assertEqual(self.render_calls, 0)
        self.assertEqual(self.event.calls, [])
        self.assertEqual(self.text_sends[0][1].text, "complete private ranked text")

    async def test_cancel_before_scheduled_task_starts_releases_all_pages(self):
        self.fixture.output._send_scheduler = _CancelBeforeRunScheduler(
            self.fixture.lifecycle
        )
        self.fixture.output._send_timeout = 0.01
        outcome = await self.fixture.output.route(self.fixture.view, self.result)
        self.assertIs(outcome.status, OutputStatus.UNKNOWN, outcome)
        self.assertEqual(self.event.calls, [])
        for _ in range(20):
            if not self.fixture.output._collage_png._entries:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.fixture.output._collage_png._entries, {})

    async def test_two_hundred_rows_get_bounded_complete_private_text(self):
        self.fixture.output._command_card_renderer = CommandCardRenderer()
        self.fixture.output._collage_scope_binder = None
        rows = tuple(
            TextBlock(f"Rank {index:03}: Example item {index}") for index in range(200)
        )
        card = Document(
            "Two hundred items",
            blocks=(
                WeightedCollage(
                    tuple(
                        CollageItem(f"item-{index}", 200 - index)
                        for index in range(200)
                    )
                ),
            ),
        )
        document = replace(self.result.document, ordered_blocks=rows, card=card)
        self.assertEqual(
            self.fixture.output._command_card_renderer(
                document, audience=DisplayAudience.PRIVATE, image=False
            ).text,
            TOO_LARGE,
        )
        outcome = await self.fixture.output.route(
            self.fixture.view, replace(self.result, document=document)
        )
        self.assertIs(outcome.status, OutputStatus.SENT, outcome)
        self.assertEqual(self.event.calls, [])
        self.assertEqual(len(self.text_sends), 1)
        message = self.text_sends[0][1].text
        self.assertIn("Rank 000: Example item 0", message)
        self.assertIn("Rank 199: Example item 199", message)
        self.assertEqual(message.count("Rank "), 200)


if __name__ == "__main__":
    unittest.main()


class CollagePngProfileTests(unittest.TestCase):
    def test_large_page_is_local_and_ordinary_one_mib_guard_remains(self):
        pixels = random.Random(101).randbytes(700 * 700 * 3)
        image = Image.frombytes("RGB", (700, 700), pixels)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        bitmap = RenderedBitmap(stream.getvalue(), 700, 700)
        self.assertGreater(len(bitmap.content), 1_048_576)
        self.assertLess(len(bitmap.content), 4_194_304)
        with self.assertRaises(PngValidationError):
            validate_png(bitmap)
        validated = validate_png(
            bitmap,
            limits=PngValidationLimits(max_bytes=4_194_304),
            collage_profile=True,
        )
        self.assertEqual(validated.size, len(bitmap.content))
        with self.assertRaises(PngValidationError):
            validate_png(
                bitmap,
                limits=PngValidationLimits(max_bytes=4_194_304),
                collage_profile="untrusted",
            )


class BoundFinishedPageCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = await build_runtime(Path(self.temp.name))
        self.active = []

    async def asyncTearDown(self):
        for view, lease in self.active:
            self.runtime.issuer.release(view)
            self.runtime.lifecycle.admission.release(lease)
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def bound(self, actor):
        registered = self.runtime.registry.snapshot().module("sample/alpha")
        view = self.runtime.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id=registered.module_id,
            module_epoch=registered.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id=actor,
            conversation_id="room",
            adapter_id="test-adapter",
            capability_id="read",
        )
        lease = self.runtime.lifecycle.admission.admit(view, "read")
        self.active.append((view, lease))
        scope = await self.runtime.services.for_module("sample/alpha").scopes.bind(view)

        async def current():
            self.runtime.issuer.require(view)
            self.runtime.lifecycle.admission.check(lease)

        return scope, current

    async def test_real_bound_user_cache_freezes_json_and_hits_same_actor_only(self):
        alice, current_alice = await self.bound("alice")
        pages = (_png("#123456"), _png("#654321"))
        await store_finished_pages(alice, "collage-real-bound", pages, current_alice)
        result = await alice.cache.lookup(
            __import__(
                "yomihime_game_link_sdk.storage", fromlist=["CacheQuery"]
            ).CacheQuery("collage-real-bound", CacheVisibility.USER)
        )
        self.assertIs(result.status, CacheLookupStatus.HIT)
        self.assertIs(type(result.entry.payload["pages"]), tuple)
        self.assertEqual(
            await load_finished_pages(alice, "collage-real-bound", current_alice),
            pages,
        )
        bob, current_bob = await self.bound("bob")
        self.assertIsNone(
            await load_finished_pages(bob, "collage-real-bound", current_bob)
        )

    async def test_revoked_current_and_permission_error_are_never_cache_misses(self):
        scope, current = await self.bound("alice")
        view, lease = self.active[-1]
        self.runtime.issuer.release(view)
        self.runtime.lifecycle.admission.release(lease)
        self.active.pop()
        with self.assertRaises(AccessDenied):
            await load_finished_pages(scope, "collage-revoked", current)
        with self.assertRaises(AccessDenied):
            await store_finished_pages(
                scope, "collage-revoked", (_png("#123456"),), current
            )
