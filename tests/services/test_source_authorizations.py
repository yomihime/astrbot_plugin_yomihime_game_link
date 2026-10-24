"""Synthetic Core authorization tests; no provider business constants."""

import asyncio
import json
import unittest
from dataclasses import asdict, replace

from ygl_test_subject.services.source_authorizations import (
    AuthorizationBinding,
    AuthorizationError,
    AuthorizationFlowPolicy,
    SourceAuthorizations,
    _encode_qr,
)

from yomihime_game_link_sdk.services import HttpResponse


def descriptor():
    return {
        "flow_id": "example_qr",
        "apply": {
            "source_id": "example_auth",
            "path": "/apply",
            "method": "POST",
            "json_body": {"app": "example"},
        },
        "poll": {
            "source_id": "example_auth",
            "path": "/check",
            "method": "POST",
            "json_body": {"app": "example"},
            "secret_json_fields": {"ticket": "ticket_slot"},
        },
        "qr_url_path": ["result", "url"],
        "ticket_capture": {
            "slot": "ticket_slot",
            "path": ["result", "url"],
            "url_query": "ticket",
        },
        "state_path": ["result", "state"],
        "pending_values": [10],
        "success_values": [20],
        "expired_values": [30],
        "success_captures": [
            {"slot": "token", "path": ["result", "token"]},
            {"slot": "subject", "path": ["result", "account"], "pattern": "[a-z-]+"},
        ],
        "subject_slot": "subject",
        "poll_interval_seconds": 2,
        "ttl_seconds": 60,
        "max_polls": 3,
    }


class AuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.binding = AuthorizationBinding("owner", "admin", "page", "runtime", 2, 4)
        self.responses = [
            {
                "result": {
                    "url": "https://identity.example.test/scan?ticket=private-ticket"
                }
            },
            {
                "result": {
                    "state": 20,
                    "token": "private-final-token",
                    "account": "user-one",
                }
            },
        ]
        self.requests, self.writes = [], []
        self.valid = True
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block_request = False
        self.fail_write = False

        async def request(request):
            self.requests.append(request)
            if self.block_request:
                self.entered.set()
                await self.release.wait()
            return HttpResponse(200, {}, json.dumps(self.responses.pop(0)).encode())

        async def guard(binding):
            if not self.valid:
                raise AuthorizationError("current_guard_failed")

        async def update(binding, captures):
            if self.fail_write:
                raise RuntimeError("unsafe error containing private-final-token")
            self.writes.append((binding, dict(captures)))

        self.runner = SourceAuthorizations(
            policies={
                "example_qr": AuthorizationFlowPolicy.from_descriptor(descriptor())
            },
            request=request,
            guard=guard,
            credential_update=update,
            clock=lambda: self.now,
            qr_encoder=lambda url: b"\x89PNG\r\n\x1a\nmock",
        )

    async def asyncTearDown(self):
        await self.runner.close()

    async def begin(self):
        return await self.runner.begin("example_qr", self.binding)

    async def test_capture_injection_atomic_update_and_redacted_view(self):
        first = await self.begin()
        self.assertEqual(first.state, "waiting")
        self.assertNotIn("private", repr(asdict(first)))
        self.assertTrue(
            (await self.runner.qr_png(first.session_id, self.binding)).startswith(
                b"\x89PNG"
            )
        )
        self.assertEqual(
            (await self.runner.poll(first.session_id, self.binding)).polls, 0
        )
        self.now += 2
        result = await self.runner.poll(first.session_id, self.binding)
        self.assertEqual(result.state, "authorized")
        self.assertEqual(result.authorized_subject, "user-one")
        self.assertEqual(
            self.writes,
            [(self.binding, {"token": "private-final-token", "subject": "user-one"})],
        )
        self.assertEqual(json.loads(self.requests[-1].body)["ticket"], "private-ticket")
        self.assertNotIn("private", repr(asdict(result)))
        self.assertFalse(self.runner._session(first.session_id, self.binding).slots)
        self.assertIsNone(self.runner._session(first.session_id, self.binding).qr_url)

    async def test_owner_page_runtime_epoch_and_revision_isolation(self):
        first = await self.begin()
        for field, value in (
            ("owner_id", "other"),
            ("actor_id", "other"),
            ("page_session_id", "other"),
            ("runtime_id", "other"),
            ("owner_epoch", 3),
            ("expected_revision", 5),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(AuthorizationError, "session_unavailable"),
            ):
                await self.runner.poll(
                    first.session_id, replace(self.binding, **{field: value})
                )
        self.assertEqual(len(self.requests), 1)

    async def test_guard_invalidation_erases_ticket(self):
        first = await self.begin()
        self.valid = False
        with self.assertRaisesRegex(AuthorizationError, "current_guard_failed"):
            await self.runner.poll(first.session_id, self.binding)
        self.assertFalse(self.runner._session(first.session_id, self.binding).slots)
        self.assertEqual(len(self.requests), 1)

    async def test_expiry_and_cancel_make_no_more_requests(self):
        first = await self.begin()
        self.now += 61
        result = await self.runner.poll(first.session_id, self.binding)
        self.assertEqual(result.state, "expired")
        self.assertFalse(self.runner._session(first.session_id, self.binding).slots)
        self.assertEqual(len(self.requests), 1)

    async def test_cancel_during_poll_prevents_final_capture(self):
        first = await self.begin()
        self.now += 2
        self.block_request = True
        task = asyncio.create_task(self.runner.poll(first.session_id, self.binding))
        await self.entered.wait()
        await self.runner.cancel(first.session_id, self.binding)
        self.release.set()
        with self.assertRaisesRegex(AuthorizationError, "session_inactive"):
            await task
        self.assertFalse(self.writes)

    async def test_guard_change_while_request_pending_prevents_capture(self):
        first = await self.begin()
        self.now += 2
        self.block_request = True
        task = asyncio.create_task(self.runner.poll(first.session_id, self.binding))
        await self.entered.wait()
        self.valid = False
        self.release.set()
        with self.assertRaisesRegex(AuthorizationError, "current_guard_failed"):
            await task
        self.assertFalse(self.writes)

    async def test_close_during_poll_prevents_capture(self):
        first = await self.begin()
        self.now += 2
        self.block_request = True
        task = asyncio.create_task(self.runner.poll(first.session_id, self.binding))
        await self.entered.wait()
        await self.runner.close()
        self.release.set()
        with self.assertRaisesRegex(AuthorizationError, "authorization_closed"):
            await task
        self.assertFalse(self.writes)

    async def test_bounded_polls_and_unknown_status(self):
        first = await self.begin()
        self.responses[:] = [{"result": {"state": 10}}] * 3
        for _ in range(3):
            self.now += 2
            result = await self.runner.poll(first.session_id, self.binding)
        self.assertEqual(result.state, "expired")
        self.assertEqual(result.polls, 3)
        self.assertFalse(self.writes)

    async def test_unrecognized_status_does_not_commit(self):
        first = await self.begin()
        self.responses[:] = [{"result": {"state": 99, "token": "private-final-token"}}]
        self.now += 2
        with self.assertRaisesRegex(AuthorizationError, "unknown_authorization_state"):
            await self.runner.poll(first.session_id, self.binding)
        self.assertFalse(self.writes)

    async def test_revoke_clears_atomic_credential_and_invalidates_sessions(self):
        first = await self.begin()
        await self.runner.revoke("example_qr", self.binding)
        self.assertEqual(self.writes, [(self.binding, {})])
        result = await self.runner.poll(first.session_id, self.binding)
        self.assertEqual(result.state, "revoked")

    async def test_callback_errors_are_sanitized_and_ticket_is_erased(self):
        first = await self.begin()
        self.now += 2
        self.fail_write = True
        with self.assertRaises(AuthorizationError) as caught:
            await self.runner.poll(first.session_id, self.binding)
        self.assertEqual(str(caught.exception), "credential_update_failed")
        self.assertFalse(self.runner._session(first.session_id, self.binding).slots)

    async def timed_session(self, update):
        """Use the production call_later expiry with a real monotonic clock."""
        old = self.runner
        await old.close()
        policy = replace(
            old._policies["example_qr"],
            ttl_seconds=0.08,
            poll_interval_seconds=0.001,
        )
        loop = asyncio.get_running_loop()
        self.runner = SourceAuthorizations(
            policies={"example_qr": policy},
            request=old._request,
            guard=old._guard,
            credential_update=update,
            clock=loop.time,
        )
        first = await self.begin()
        session = self.runner._session(first.session_id, self.binding)
        elapsed = asyncio.Event()
        # This later observer runs only after the real production expiry timer.
        observer = loop.call_at(session.expiry_timer.when() + 0.005, elapsed.set)
        self.addCleanup(observer.cancel)
        await asyncio.sleep(0.002)
        return first, session, elapsed

    async def test_real_expiry_during_commit_reports_success_and_cancel_waits(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def update(binding, material):
            entered.set()
            await release.wait()
            # The Host writer still owns its final current/CAS transaction fence.
            await self.runner._guard(binding)
            self.writes.append((binding, dict(material)))

        first, session, elapsed = await self.timed_session(update)
        poll = asyncio.create_task(self.runner.poll(first.session_id, self.binding))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertFalse(session.slots)
        self.assertIsNone(session.qr_url)
        cancel = asyncio.create_task(
            self.runner.cancel(first.session_id, self.binding)
        )
        await asyncio.wait_for(elapsed.wait(), 1)
        self.assertFalse(session.expiry_timer.cancelled())
        # _session also checks wall time; it must honor commit admission too.
        self.assertEqual(
            self.runner._session(first.session_id, self.binding).state, "waiting"
        )
        self.assertTrue(session.committing)
        self.assertFalse(cancel.done())
        release.set()
        result, cancelled = await asyncio.wait_for(asyncio.gather(poll, cancel), 1)
        self.assertEqual(result.state, "authorized")
        self.assertEqual(cancelled.state, "authorized")
        self.assertEqual(result.authorized_subject, "user-one")
        self.assertEqual(len(self.writes), 1)
        self.assertFalse(session.committing)

    async def test_real_expiry_does_not_turn_failed_commit_into_success(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def update(binding, material):
            entered.set()
            await release.wait()
            raise RuntimeError("private-final-token failed to persist")

        first, session, elapsed = await self.timed_session(update)
        task = asyncio.create_task(self.runner.poll(first.session_id, self.binding))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(elapsed.wait(), 1)
        release.set()
        with self.assertRaisesRegex(AuthorizationError, "^credential_update_failed$"):
            await asyncio.wait_for(task, 1)
        self.assertEqual(session.state, "error")
        self.assertIsNone(session.subject)
        self.assertFalse(session.slots)
        self.assertFalse(self.writes)

    async def test_real_expiry_does_not_bypass_invalidated_host_commit_fence(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def update(binding, material):
            entered.set()
            await release.wait()
            await self.runner._guard(binding)
            self.writes.append((binding, dict(material)))

        first, session, elapsed = await self.timed_session(update)
        task = asyncio.create_task(self.runner.poll(first.session_id, self.binding))
        await asyncio.wait_for(entered.wait(), 1)
        self.valid = False
        self.runner.invalidate(owner_id="owner", page_session_id="page")
        await asyncio.wait_for(elapsed.wait(), 1)
        release.set()
        with self.assertRaisesRegex(AuthorizationError, "^credential_update_failed$"):
            await asyncio.wait_for(task, 1)
        self.assertEqual(session.state, "cancelled")
        self.assertIsNone(session.subject)
        self.assertFalse(session.slots)
        self.assertFalse(self.writes)

    async def test_real_expiry_before_commit_admission_still_prevents_write(self):
        first, session, elapsed = await self.timed_session(
            self.runner._credential_update
        )
        self.block_request = True
        task = asyncio.create_task(self.runner.poll(first.session_id, self.binding))
        await asyncio.wait_for(self.entered.wait(), 1)
        await asyncio.wait_for(elapsed.wait(), 1)
        self.assertEqual(session.state, "expired")
        self.release.set()
        with self.assertRaisesRegex(AuthorizationError, "^session_inactive$"):
            await asyncio.wait_for(task, 1)
        self.assertEqual(session.state, "expired")
        self.assertFalse(session.slots)
        self.assertFalse(self.writes)

    async def test_malformed_capture_and_unsafe_url_fail_closed(self):
        self.responses[:] = [
            {"result": {"url": "https://identity.example.test/?ticket=a&ticket=b"}}
        ]
        with self.assertRaisesRegex(AuthorizationError, "invalid_response"):
            await self.begin()
        self.assertFalse(self.writes)

    async def test_lineage_invalidation_erases_only_matching_pending_session(self):
        first = await self.begin()
        self.runner.invalidate(page_session_id="different-lineage")
        self.assertEqual(
            self.runner._session(first.session_id, self.binding).state, "waiting"
        )
        self.runner.invalidate(owner_id="owner", page_session_id="page")
        result = await self.runner.poll(first.session_id, self.binding)
        self.assertEqual(result.state, "cancelled")
        self.assertNotIn(first.session_id, self.runner._sessions)
        self.assertFalse(self.runner._session(first.session_id, self.binding).slots)

    async def test_session_table_and_terminal_history_are_bounded(self):
        response = {
            "result": {"url": "https://identity.example.test/scan?ticket=synthetic"}
        }
        self.responses[:] = [response] * 260
        for index in range(128):
            binding = replace(self.binding, page_session_id=f"page-{index}")
            await self.runner.begin("example_qr", binding)
        self.assertEqual(len(self.runner._sessions), 128)
        with self.assertRaisesRegex(
            AuthorizationError, "authorization_capacity_exceeded"
        ):
            await self.runner.begin(
                "example_qr", replace(self.binding, page_session_id="page-overflow")
            )
        self.runner.invalidate()
        self.assertEqual(len(self.runner._sessions), 0)
        for index in range(129):
            binding = replace(self.binding, page_session_id=f"replacement-{index}")
            view = await self.runner.begin("example_qr", binding)
            await self.runner.cancel(view.session_id, binding)
        self.assertEqual(len(self.runner._sessions), 0)
        self.assertEqual(len(self.runner._terminal), 128)

    def test_real_qr_encoder_produces_a_decodable_png(self):
        import io

        from PIL import Image

        png = _encode_qr("https://identity.example.test/scan?ticket=synthetic-fixture")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(png)) as rendered:
            rendered.load()
            self.assertEqual(rendered.format, "PNG")
            self.assertEqual(rendered.width, rendered.height)
            self.assertGreater(rendered.width, 100)

    def test_policy_rejects_arbitrary_urls_and_conflicting_states(self):
        for path in (
            "https://example.test/check",
            "//example.test/check",
            "/../check",
            "/x%2fcheck",
        ):
            data = descriptor()
            data["poll"]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                AuthorizationFlowPolicy.from_descriptor(data)
        data = descriptor()
        data["success_values"] = data["pending_values"]
        with self.assertRaises(ValueError):
            AuthorizationFlowPolicy.from_descriptor(data)
        for key, value in (("ttl_seconds", 301), ("max_polls", 151)):
            data = descriptor()
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                AuthorizationFlowPolicy.from_descriptor(data)
