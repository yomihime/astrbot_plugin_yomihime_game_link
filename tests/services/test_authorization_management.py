"""Join real page lineage, manager routing and runner using synthetic transport.

Transport responses and CAS storage are deterministic test doubles. The actual
page registry, ContextVar management routing and authorization runner are used.
"""

import asyncio
import base64
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.page_sessions import HostModulePageSessions
from ygl_test_subject.core.contracts.administration import AdminAuthorizationDenied
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.services import authorization_management as management_module
from ygl_test_subject.services.authorization_management import AuthorizationManagement
from ygl_test_subject.services.module_pages import ModulePageGrant
from ygl_test_subject.services.source_authorizations import (
    AuthorizationBinding,
    AuthorizationError,
    SourceAuthorizations,
)

from tests.services.test_source_authorizations import descriptor
from yomihime_game_link_sdk.services import HttpResponse

OWNER_A = "owner-a/demo"
OWNER_B = "owner-b/demo"


class _PrivateTransport:
    def __init__(self, test, owner):
        self.test, self.owner = test, owner

    async def fetch(self, request, *, _check_authority, _authorization_request):
        test = self.test
        test.assertTrue(_authorization_request)
        await _check_authority()
        context = test.management._context()
        test.assertIs(context["http"], self)
        test.assertEqual(context["binding"].owner_id, self.owner)
        test.requests.append((self.owner, request))
        # Force interleaving so ContextVar isolation is exercised, rather than
        # allowing an entirely synchronous fake to conceal routing mistakes.
        await asyncio.sleep(0)
        if self.owner in test.block_http and request.path.endswith("/poll"):
            test.http_entered.set()
            await test.http_release.wait()
        await _check_authority()
        test.assertIs(test.management._context()["http"], self)
        flow = request.path.split("/")[1]
        if request.path.endswith("/apply"):
            payload = {
                "result": {
                    "url": f"https://identity.example.test/scan?ticket=synthetic-{flow}"
                }
            }
        else:
            test.assertEqual(json.loads(request.body)["ticket"], f"synthetic-{flow}")
            payload = {"result": {"state": 10}}
            if (self.owner, flow) in test.success:
                payload = {
                    "result": {
                        "state": 20,
                        "token": f"synthetic-token-{self.owner}-{flow}",
                        "account": "subject-a"
                        if self.owner == OWNER_A
                        else "subject-b",
                    }
                }
        return HttpResponse(200, {}, json.dumps(payload).encode())


class AuthorizationManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.registry = HostModulePageSessions(wall_clock=lambda: self.now)
        self.grants = {
            owner: ModulePageGrant(
                owner,
                "settings",
                "runtime-a",
                3,
                frozenset({"ordinary"}),
                frozenset({"credential_a", "credential_b"}),
            )
            for owner in (OWNER_A, OWNER_B)
        }
        self.digest = self.registry.bearer_digest("synthetic-host-session")
        self.revisions = {OWNER_A: 7, OWNER_B: 7}
        self.credentials = {
            OWNER_A: {"token": "previous-token", "subject": "previous-subject"},
            OWNER_B: {},
        }
        self.requests, self.commits = [], []
        self.success, self.block_http, self.block_commit = set(), set(), set()
        self.http_entered, self.http_release = asyncio.Event(), asyncio.Event()
        self.commit_entered, self.commit_release = asyncio.Event(), asyncio.Event()
        self.valid = True
        definitions = {}
        for owner in self.grants:
            definitions[owner] = {}
            for flow in ("flow_a", "flow_b"):
                row = deepcopy(descriptor())
                row["flow_id"] = flow
                row["apply"]["path"] = f"/{flow}/apply"
                row["poll"]["path"] = f"/{flow}/poll"
                row["max_polls"] = 40
                definitions[owner][flow] = {
                    "credential_field": "credential_a"
                    if flow == "flow_a"
                    else "credential_b",
                    "descriptor": row,
                }
        self.factory_patch = patch.object(
            management_module,
            "SourceAuthorizations",
            side_effect=lambda **kwargs: SourceAuthorizations(
                **kwargs,
                clock=lambda: self.now,
                qr_encoder=lambda url: b"\x89PNG\r\n\x1a\nsynthetic-encoder",
            ),
        )
        self.factory_patch.start()
        self.management = AuthorizationManagement(
            SimpleNamespace(authorizations=definitions)
        )
        self.transports = {
            owner: _PrivateTransport(self, owner) for owner in self.grants
        }
        self.proofs = {owner: self.issue(owner) for owner in self.grants}

    async def asyncTearDown(self):
        await self.management.close()
        self.registry.close()
        self.factory_patch.stop()

    def authority(self, owner):
        return {
            "subject": "synthetic-admin",
            "bearer_digest": self.digest,
            "binding": self.grants[owner],
            "generation": 1,
        }

    def issue(self, owner=OWNER_A, previous=None):
        return self.registry.issue(
            **self.authority(owner), expiry=self.now + 30, previous_session=previous
        )["page_session"]

    def rotate(self, owner=OWNER_A):
        previous = self.proofs[owner]
        lineage = self.registry.require(previous, **self.authority(owner)).lineage_id
        current = self.issue(owner, previous)
        self.proofs[owner] = current
        self.assertEqual(
            self.registry.require(current, **self.authority(owner)).lineage_id, lineage
        )
        with self.assertRaises(AdminAuthorizationDenied):
            self.registry.require(previous, **self.authority(owner))

    async def execute(
        self,
        step,
        *,
        owner=OWNER_A,
        flow="flow_a",
        session="",
        proof=None,
        revision=None,
    ):
        proof = proof or self.proofs[owner]
        page = self.registry.require(proof, **self.authority(owner))
        expected = self.revisions[owner] if revision is None else revision
        binding = AuthorizationBinding(
            owner,
            "synthetic-admin:" + self.digest.hex(),
            page.lineage_id,
            self.grants[owner].runtime_id,
            self.grants[owner].module_epoch,
            expected,
        )

        async def guard():
            self.registry.require(proof, **self.authority(owner))
            if not self.valid:
                raise AdminAuthorizationDenied
            if self.revisions[owner] != expected:
                raise RevisionConflict(
                    "source.authorization", expected, self.revisions[owner]
                )

        async def commit(material):
            await guard()
            if owner in self.block_commit:
                self.commit_entered.set()
                await self.commit_release.wait()
            # Models the Host transaction fence immediately before the atomic
            # token + subject write; no partial field writes are possible here.
            await guard()
            self.credentials[owner] = dict(material)
            self.revisions[owner] += 1
            self.commits.append((owner, flow, expected, dict(material)))
            return {"revision": self.revisions[owner]}

        return await self.management.execute(
            binding=binding,
            flow_id=flow,
            step=step,
            session_id=session,
            guard=guard,
            commit=commit,
            http=self.transports[owner],
        )

    async def begin(self, owner=OWNER_A, flow="flow_a"):
        result = await self.execute("begin", owner=owner, flow=flow)
        self.assertIsNone(result["receipt"])
        self.assertIsNone(self.management._current.get())
        return result["session"]["session_id"]

    async def test_qr_polling_uses_rotated_lineage_and_commits_one_atomic_cas_receipt(
        self,
    ):
        session = await self.begin()
        qr = await self.execute("qr", session=session)
        self.assertTrue(base64.b64decode(qr["qr_png"]).startswith(b"\x89PNG"))
        for elapsed in range(2, 60, 2):
            self.now = 1000 + elapsed
            if elapsed in (20, 40):
                self.rotate()
            if elapsed == 58:
                self.success.add((OWNER_A, "flow_a"))
            result = await self.execute("poll", session=session)
            self.assertIsNone(self.management._current.get())
        self.assertEqual(result["session"]["state"], "authorized")
        self.assertEqual(result["receipt"], {"revision": 8})
        self.assertEqual(len(self.commits), 1)
        self.assertEqual(
            self.credentials[OWNER_A],
            {"token": "synthetic-token-owner-a/demo-flow_a", "subject": "subject-a"},
        )
        self.assertNotIn("synthetic-token", repr(result))
        self.assertNotIn("ticket", repr(result))
        self.now = 1060
        self.rotate()  # The page remains continuous beyond the 60s QR lifetime.

    async def test_closed_flow_and_session_crossing_never_routes_to_http_or_commit(
        self,
    ):
        session = await self.begin()
        requests = len(self.requests)
        for kwargs in ({"flow": "flow_b"}, {"owner": OWNER_B}, {"flow": "unknown"}):
            for step in ("poll", "cancel", "qr"):
                with (
                    self.subTest(kwargs=kwargs, step=step),
                    self.assertRaises(AuthorizationError),
                ):
                    await self.execute(step, session=session, **kwargs)
        self.assertEqual(len(self.requests), requests)
        self.assertFalse(self.commits)
        self.assertIsNone(self.management._current.get())

    async def test_a_new_view_cannot_inherit_session_but_a_rotated_view_can_cancel(
        self,
    ):
        session = await self.begin()
        other = self.issue()
        with self.assertRaisesRegex(AuthorizationError, "session_unavailable"):
            await self.execute("poll", session=session, proof=other)
        self.now += 20
        self.rotate()
        result = await self.execute("cancel", session=session)
        self.assertEqual(result["session"]["state"], "cancelled")
        self.assertFalse(self.commits)

    async def test_concurrent_owners_keep_contextvar_transport_guard_and_receipts_separate(
        self,
    ):
        first, second = await asyncio.gather(self.begin(OWNER_A), self.begin(OWNER_B))
        self.now += 2
        self.success.update({(OWNER_A, "flow_a"), (OWNER_B, "flow_a")})
        results = await asyncio.gather(
            self.execute("poll", owner=OWNER_A, session=first),
            self.execute("poll", owner=OWNER_B, session=second),
        )
        self.assertEqual(
            [row["session"]["authorized_subject"] for row in results],
            ["subject-a", "subject-b"],
        )
        self.assertEqual(
            [row["receipt"] for row in results], [{"revision": 8}, {"revision": 8}]
        )
        self.assertEqual({row[0] for row in self.commits}, {OWNER_A, OWNER_B})
        self.assertIsNone(self.management._current.get())
        with self.assertRaisesRegex(AuthorizationError, "session_unavailable"):
            self.management._context()

    async def test_cancellation_during_poll_uses_its_own_request_context_and_never_commits(
        self,
    ):
        session = await self.begin()
        self.now += 2
        self.success.add((OWNER_A, "flow_a"))
        self.block_http.add(OWNER_A)
        polling = asyncio.create_task(self.execute("poll", session=session))
        await self.http_entered.wait()
        result = await self.execute("cancel", session=session)
        self.assertEqual(result["session"]["state"], "cancelled")
        self.http_release.set()
        with self.assertRaises(AuthorizationError):
            await polling
        self.assertFalse(self.commits)
        self.assertIsNone(self.management._current.get())

    async def test_cas_change_while_response_is_pending_keeps_previous_credentials(
        self,
    ):
        session = await self.begin()
        self.now += 2
        self.success.add((OWNER_A, "flow_a"))
        self.block_http.add(OWNER_A)
        before = dict(self.credentials[OWNER_A])
        polling = asyncio.create_task(self.execute("poll", session=session))
        await self.http_entered.wait()
        self.revisions[OWNER_A] += 1
        self.http_release.set()
        with self.assertRaises(AuthorizationError):
            await polling
        self.assertEqual(self.credentials[OWNER_A], before)
        self.assertFalse(self.commits)

    async def test_rotating_proof_during_pending_request_does_not_extend_old_authority(
        self,
    ):
        session = await self.begin()
        self.now += 2
        self.success.add((OWNER_A, "flow_a"))
        self.block_http.add(OWNER_A)
        polling = asyncio.create_task(self.execute("poll", session=session))
        await self.http_entered.wait()
        self.rotate()
        self.http_release.set()
        with self.assertRaises(AuthorizationError):
            await polling
        self.assertFalse(self.commits)
        self.registry.require(self.proofs[OWNER_A], **self.authority(OWNER_A))
        self.assertIsNone(self.management._current.get())

    async def test_atomic_commit_fence_rejects_a_newer_credential_revision(self):
        session = await self.begin()
        self.now += 2
        self.success.add((OWNER_A, "flow_a"))
        self.block_commit.add(OWNER_A)
        before = dict(self.credentials[OWNER_A])
        polling = asyncio.create_task(self.execute("poll", session=session))
        await self.commit_entered.wait()
        self.revisions[OWNER_A] += 1
        self.commit_release.set()
        with self.assertRaisesRegex(AuthorizationError, "credential_update_failed"):
            await polling
        self.assertEqual(self.credentials[OWNER_A], before)
        self.assertEqual(self.revisions[OWNER_A], 8)
        self.assertFalse(self.commits)

    async def test_expired_proof_bootstrap_cannot_resume_an_old_authorization(self):
        session = await self.begin()
        previous = self.proofs[OWNER_A]
        self.now += 30
        with self.assertRaises(AdminAuthorizationDenied):
            self.issue(previous=previous)
        self.proofs[OWNER_A] = self.issue()
        with self.assertRaisesRegex(AuthorizationError, "session_unavailable"):
            await self.execute("poll", session=session)
        self.assertFalse(self.commits)

    async def test_lineage_invalidation_and_revoke_leave_no_pending_credential_capture(
        self,
    ):
        session = await self.begin()
        page = self.registry.require(self.proofs[OWNER_A], **self.authority(OWNER_A))
        self.management.invalidate(page_session_id=page.lineage_id)
        result = await self.execute("poll", session=session)
        self.assertEqual(result["session"]["state"], "cancelled")
        revoked = await self.execute("revoke")
        self.assertTrue(revoked["revoked"])
        self.assertEqual(revoked["receipt"], {"revision": 8})
        self.assertEqual(self.credentials[OWNER_A], {})
        self.assertIsNone(self.management._current.get())

    async def test_rejected_context_is_reset_and_does_not_contaminate_a_later_request(
        self,
    ):
        self.valid = False
        with self.assertRaises(AdminAuthorizationDenied):
            await self.execute("begin")
        self.assertIsNone(self.management._current.get())
        self.assertFalse(self.requests)
        self.valid = True
        session = await self.begin()
        self.assertTrue(session)
