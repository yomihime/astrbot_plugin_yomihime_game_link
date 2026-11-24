"""Prove what a current player grant does at the real public HTTP boundary."""

import asyncio
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp
from ygl_test_subject.core.contracts.services import Grant, GrantStatus
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import SecretOwner
from ygl_test_subject.infrastructure.http import SourceHttpError
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.services.authorization import (
    AuthorizationError,
    AuthorizationPermissionError,
    AuthorizationService,
)
from ygl_test_subject.services.module_services import ModuleServicesFactory
from ygl_test_subject.services.player_source import PlayerReadPolicy

from tests.fixtures.b03_runtime import build_runtime
from yomihime_game_link_sdk.contexts import InvocationConversationKind, InvocationOrigin
from yomihime_game_link_sdk.services import HttpRequest
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef


class PlayerSourceBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_verified_owner_grant_grants_private_access_but_no_http_credential(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = await build_runtime(root)
            self.addAsyncCleanup(runtime.database.executor.close)
            module_id = "sample/alpha"
            repo = runtime.repositories
            material = secrets.token_urlsafe(32).encode("ascii")
            one_time = {}

            class IsolatedKey:
                key = secrets.token_bytes(32)

                def get_key(self):
                    return self.key

            repo.secret_store = SQLiteSecretStore(
                runtime.database,
                root / "player-secrets",
                codec=AESGCMSecretCodec(IsolatedKey()),
            )
            services = ModuleServicesFactory(
                runtime.registry,
                runtime.issuer,
                runtime.lifecycle,
                repo,
                runtime.transport,
                config_principal_id="host-config",
                identity_namespace="test-users",
            )
            public = services.for_module(module_id)
            policy = PlayerReadPolicy(
                module_id, "catalog", "example.test", "/items", "private.read"
            )
            await repo.conversations.save(
                validate_contract(
                    ConversationRef(
                        "test-adapter", ConversationKind.DIRECT, "dm", "test-dm-route"
                    )
                )
            )

            def invocation(
                actor,
                cap="read",
                grant=None,
                room="dm",
                kind=InvocationConversationKind.DIRECT,
            ):
                state = runtime.registry.snapshot()
                result = runtime.issuer.issue(
                    origin=InvocationOrigin.COMMAND,
                    module_id=module_id,
                    module_epoch=state.module(module_id).epoch,
                    registry_revision=state.revision,
                    actor_id=actor,
                    adapter_id="test-adapter",
                    conversation_id=room,
                    conversation_kind=kind,
                    capability_id=cap,
                    grant_id=grant.grant_id if grant else None,
                    grant_revision=grant.revision if grant else None,
                )
                runtime.lifecycle.admission.admit(result, cap)
                return result

            async def verify(session, code):
                # Model only the external provider. It attests a specific player and
                # session once; neither the command nor the module supplies a Grant.
                if one_time.get(code) != (
                    session.principal_id,
                    session.module_id,
                    session.session_id,
                ):
                    raise ValueError("invalid provider confirmation")
                del one_time[code]
                owner = SecretOwner(
                    session.principal_id,
                    session.module_id,
                    policy.field,
                    session.session_id,
                )
                receipt = await repo.secret_store.put(material, owner=owner)
                return Grant(
                    secrets.token_hex(16),
                    1,
                    owner.principal_id,
                    owner.module_id,
                    "external-player",
                    (policy.scope,),
                    receipt.secret_ref,
                    GrantStatus.ACTIVE,
                )

            def make_auth(verifier=None):
                return AuthorizationService(
                    repo.authorization,
                    repo.authorization,
                    runtime.issuer,
                    identity_repository=repo.identities,
                    conversation_repository=repo.conversations,
                    module_lookup=repo.registration_lookup,
                    identity_namespace="test-users",
                    revocation_coordinator=repo.grant_revocation,
                    secret_store=repo.secret_store,
                    exchange_verifier=verifier,
                    admission=runtime.lifecycle.admission,
                )

            alice, bob = invocation("alice"), invocation("bob")
            absent = make_auth()
            sid = await absent.begin_login(alice)
            with self.assertRaises(AuthorizationPermissionError):
                await absent.complete_login(alice, sid, "not-provider-proof")

            auth = make_auth(verify)
            sid = await auth.begin_login(alice)
            code = secrets.token_urlsafe()
            one_time[code] = ("alice", module_id, sid)
            with self.assertRaises(AuthorizationError):
                await auth.complete_login(bob, sid, code)
            reference = await auth.complete_login(alice, sid, code)
            with self.assertRaises(AuthorizationError):
                await auth.complete_login(alice, sid, code)
            granted = invocation("alice", "private.read", reference)
            self.assertEqual(
                (await auth.require_current_grant(granted)).account_id,
                "external-player",
            )
            with self.assertRaises(AuthorizationError):
                await auth.require_current_grant(
                    invocation("bob", "private.read", reference)
                )

            scope = await public.scopes.bind(granted)
            request = validate_contract(HttpRequest("catalog", "/items"))
            response = await scope.http.fetch(request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(runtime.transport.requests), 1)
            sent = runtime.transport.requests[0]
            self.assertEqual(sent.url, "https://example.test/items")
            self.assertIsNone(sent.credential_ref)
            self.assertFalse(
                {"cookie", "authorization"} & {key.lower() for key in sent.headers}
            )
            self.assertNotIn(material.decode(), str(dict(sent.headers)))
            transport = AioHttpTransport()
            self.addAsyncCleanup(transport.close)
            opted_in = ModuleServicesFactory(
                runtime.registry,
                runtime.issuer,
                runtime.lifecycle,
                repo,
                transport,
                config_principal_id="host-config",
                identity_namespace="test-users",
                player_read_policies=(policy,),
            )
            private = opted_in.for_module(module_id)
            scoped = await private.scopes.bind(granted)
            samples = []
            connector_ready = asyncio.Event()
            release_connector = asyncio.Event()
            release_connector.set()
            status = 200

            async def controlled_socket(connector, req, traces, timeout):
                self.assertEqual(str(req.url), "https://example.test/items")
                self.assertIs(req.ssl, True)
                connector_ready.set()
                await release_connector.wait()
                loop = asyncio.get_running_loop()
                proto = connector._factory()
                raw_body = b'{"echo":"' + material + b'"}'
                reply = (
                    f"HTTP/1.1 {status} Test\r\n".encode()
                    + b"Content-Type: application/json\r\nConnection: close\r\nLocation: https://other.test/\r\nContent-Length: "
                    + str(len(raw_body)).encode()
                    + b"\r\n\r\n"
                    + raw_body
                )

                class Peer(asyncio.Transport):
                    closed = False

                    def write(self, data):
                        if b"\r\n\r\n" in data:
                            samples.append(
                                (
                                    data.startswith(b"GET /items HTTP/1.1\r\n"),
                                    b"Authorization: Bearer " + material + b"\r\n"
                                    in data,
                                    b"Cookie:" not in data,
                                )
                            )
                            loop.call_soon(proto.data_received, reply)

                    def is_closing(self):
                        return self.closed

                    def close(self):
                        if not self.closed:
                            self.closed = True
                            loop.call_soon(proto.connection_lost, None)

                    abort = close

                proto.connection_made(Peer())
                return proto

            with patch.object(
                aiohttp.TCPConnector, "_create_connection", controlled_socket
            ):
                reply = await scoped.http.fetch_player(request)
                self.assertEqual(samples, [(True, True, True)])
                self.assertEqual(reply.body, b'{"echo":"[redacted]"}')
                for bad in (
                    HttpRequest("catalog", "/elsewhere"),
                    HttpRequest("catalog", "/items", "POST"),
                    HttpRequest("catalog", "/items", query=(("user", "other"),)),
                    HttpRequest("catalog", "/items", headers={"Accept": "text/plain"}),
                ):
                    with self.assertRaises(SourceHttpError):
                        await scoped.http.fetch_player(validate_contract(bad))
                for view in (
                    alice,
                    invocation("alice", "private.read", reference, "room"),
                    invocation("alice", "private.read", reference, "dm", None),
                    invocation(
                        "alice",
                        "private.read",
                        reference,
                        "dm",
                        InvocationConversationKind.GROUP,
                    ),
                ):
                    wrong_scope = await private.scopes.bind(view)
                    with self.assertRaises(SourceHttpError):
                        await wrong_scope.http.fetch_player(request)
                self.assertEqual(len(samples), 1)
                for status in (302, 401):
                    with self.assertRaises(SourceHttpError):
                        await scoped.http.fetch_player(request)
                self.assertEqual(len(samples), 3)
                connector_ready.clear()
                release_connector.clear()
                pending = asyncio.create_task(scoped.http.fetch_player(request))
                await asyncio.wait_for(connector_ready.wait(), 5)
                await runtime.lifecycle.stop(module_id)
                release_connector.set()
                with self.assertRaises(SourceHttpError):
                    await pending
                self.assertEqual(len(samples), 3)
                op = secrets.token_hex(16)
                identity, _ = await runtime.lifecycle.start_candidate(module_id, op)
                await runtime.health.prepare(
                    module_id, runtime.registry.snapshot().module(module_id).manifest
                )
                runtime.lifecycle.publish_committed_intent(
                    module_id, op, identity, True, runtime.registry.snapshot().revision
                )
                alice = invocation("alice")
                granted = invocation("alice", "private.read", reference)
                public = services.for_module(module_id)
                scope = await public.scopes.bind(granted)
                scoped = await opted_in.for_module(module_id).scopes.bind(granted)
                connector_ready.clear()
                release_connector.clear()
                pending = asyncio.create_task(scoped.http.fetch_player(request))
                await asyncio.wait_for(connector_ready.wait(), 5)
                await public.accounts.revoke(alice, reference)
                release_connector.set()
                with self.assertRaises(SourceHttpError):
                    await pending
                self.assertEqual(len(samples), 3)
            with self.assertRaises(AuthorizationError):
                await auth.require_current_grant(granted)
            with self.assertRaises(SourceHttpError):
                await scope.http.fetch(request)
            self.assertEqual(len(runtime.transport.requests), 1)
            await services.close_credentials()
            await runtime.database.executor.close()
