import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.runtime import (
    _AstrBotMessageIngress,
    _MessageReceipts,
)
from ygl_test_subject.core.contracts.administration import AdminOperation
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.player_source import PlayerReadPolicy

from tests.host.test_astrbot_runtime import _Event
from tests.host.test_player_source_ingress import manifest, probe
from tests.modules.minimal import test_formal_chain as minimal
from tests.modules.minimal.test_formal_chain import OWNER, SOURCE
from tests.services.test_admin_operations import _MessagePort, _Renderer
from yomihime_game_link_sdk.display import DisplayLimits


class CoreIngress(unittest.IsolatedAsyncioTestCase):
    enable = minimal.MinimalFormalChainTests.enable

    async def prepare(self, transform=probe, issuer=None, transport_override=None):
        tmp = self.enterContext(tempfile.TemporaryDirectory(dir=Path.cwd()))
        root = Path(tmp)
        pkg = root / "extensions/minimal"
        shutil.copytree(SOURCE, pkg)
        spec = pkg / "yomihime.manifest.json"
        data = json.loads(spec.read_bytes())
        manifest(data)
        if issuer:
            data["modules"][0]["commands"][0]["parameter_mapping"] = {
                k: k for k in ("action", "code", "sid", "path")
            }
            data["modules"][0]["capabilities"][0]["input_schema"] = {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "code": {"type": "string"},
                    "sid": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            }
        data["modules"][0]["pages"] = []
        data["modules"][0]["resources"] = []
        spec.write_text(json.dumps(data), "utf8")
        source = pkg / "module.py"
        source.write_bytes(
            transform(source.read_bytes()).replace(
                b"from ._api_v2.services import HttpRequest",
                b"from yomihime_game_link_sdk.services import HttpRequest",
            )
        )
        receiver = _AstrBotMessageIngress(
            object(), _MessageReceipts(), lambda *_: False
        )
        self.external = 0

        async def transport(*a, **kw):
            self.external += 1
            raise AssertionError("unexpected external request")

        import secrets

        key = secrets.token_bytes(32)
        from yomihime_game_link_sdk.services import CapabilityHealth, HealthStatus

        async def source_health(module, source):
            self.assertEqual((module, source), (OWNER, "catalog"))
            return CapabilityHealth(HealthStatus.AVAILABLE)

        from ygl_test_subject.extensions.discovery import discover_packages

        discovered = discover_packages(root / "extensions")
        self.assertTrue(
            discovered and discovered[0].valid,
            str(getattr(discovered[0], "diagnostic", None)),
        )
        self.core = CoreRuntime(
            source_health=source_health,
            database=root / "db.sqlite",
            extension_root=root / "extensions",
            file_root=root / "files",
            secret_root=root / "secrets",
            secret_codec=AESGCMSecretCodec(SimpleNamespace(get_key=lambda: key)),
            http_transport=transport_override or transport,
            renderer=_Renderer(),
            display_limits=validate_contract(DisplayLimits(2, 4096)),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=receiver.validate,
            host_message_current_validator=receiver.current,
            config_principal_id="fixture",
            identity_namespace="fixture",
            pump_interval=3600,
            cleanup_timeout=1,
            managed_module_owners={OWNER},
            player_issuers=(issuer,) if issuer else (),
            player_read_policies=(
                PlayerReadPolicy(
                    OWNER, "catalog", "source.example.test", "/profile", "status"
                ),
            ),
        )
        await self.core.start()
        self.addAsyncCleanup(self.core.close, timeout=1)
        self.source = self.core.admin_authorization.register_source(
            "player-test",
            resources=self.core.admin_operations.module_resources(),
            operations={AdminOperation.SET_ENABLED},
        )
        await self.enable(True)
        return receiver

    async def test_two_real_ingresses_no_automatic_grant(self):
        receiver = await self.prepare()
        for who in ("alice", "bob"):
            ev = _Event("FriendMessage", message="/ygl minimal status")
            ev.sender = who
            ev.conversation = who
            ingress = receiver.make(ev)
            self.assertIsNone(ingress.grant_reference)
            await self.core.invoke_command(OWNER, "status", {}, ingress=ingress)
        mod = self.core.registry.snapshot().module(OWNER)
        handler = mod.handlers.capabilities["status"]
        import sys

        observed = sys.modules[type(handler).__module__].OBS
        self.assertEqual(len(observed), 2, observed)
        for row, who in zip(observed, ("alice", "bob")):
            self.assertEqual(
                row,
                dict(
                    has_grant=False,
                    kind="direct",
                    actor="test-platform-1:" + who,
                    has_status=False,
                    began=True,
                    denied="SourceHttpError",
                ),
            )
        self.assertEqual(self.external, 0)

    async def test_real_issuer_http_two_players_rotation_and_purpose(self):
        import datetime
        import secrets
        import socket
        import ssl
        import sys
        import time
        from unittest.mock import patch

        import aiohttp
        import jwt
        from aiohttp import web
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519
        from cryptography.x509.oid import NameOID
        from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
        from ygl_test_subject.services.player_issuer import PlayerTicketIssuer

        policy = PlayerReadPolicy(
            OWNER, "catalog", "source.example.test", "/profile", "status"
        )
        key = ed25519.Ed25519PrivateKey.generate()
        cert_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "source.example.test")]
        )
        now = datetime.datetime.now(datetime.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(cert_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("identity.example.test"),
                        x509.DNSName("source.example.test"),
                    ]
                ),
                False,
            )
            .sign(cert_key, hashes.SHA256())
        )
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        certfile, keyfile = temp / "test-ca.pem", temp / "peer-key.pem"
        certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        keyfile.write_bytes(
            cert_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.load_cert_chain(certfile, keyfile)
        client_tls = ssl.create_default_context(cafile=str(certfile))
        codes = {}
        hits = []
        received = asyncio.Event()
        release = asyncio.Event()
        late = False

        async def exchange(request):
            values = await request.post()
            code = codes.pop(values.get("code"), None)
            if (
                code is None
                or values.get("nonce") != code[0]
                or values.get("client_id") != "test-client"
            ):
                return web.json_response({}, status=403)
            sid, sub, purpose = code
            stamp = int(time.time())
            token = jwt.encode(
                {
                    "sub": sub,
                    "iss": issuer_url,
                    "aud": "https://source.example.test",
                    "nonce": sid,
                    "client_id": "test-client",
                    "scope": purpose,
                    "iat": stamp,
                    "exp": stamp + 300,
                },
                key,
                algorithm="EdDSA",
            )
            return web.json_response({"access_token": token, "token_type": "Bearer"})

        async def profile(request):
            self.assertEqual(request.path_qs, "/profile")
            self.assertNotIn("Cookie", request.headers)
            token = request.headers["Authorization"].removeprefix("Bearer ")
            claims = jwt.decode(
                token,
                key.public_key(),
                algorithms=["EdDSA"],
                audience="https://source.example.test",
                issuer=issuer_url,
            )
            hits.append(claims["sub"])
            if late:
                received.set()
                await release.wait()
            return web.json_response({"owned": claims["sub"]})

        app = web.Application()
        app.router.add_post("/confirm", exchange)
        app.router.add_get("/profile", profile)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_tls)
        await site.start()
        port = runner.addresses[0][1]
        issuer_url = f"https://identity.example.test:{port}"
        config = PlayerTicketIssuer(
            policy,
            issuer_url,
            issuer_url + "/confirm",
            "test-client",
            "profile.read",
            key.public_key().public_bytes_raw(),
            client_tls,
        )

        async def resolve_local(connector, host, port_arg, traces=None):
            if host not in ("identity.example.test", "source.example.test"):
                raise AssertionError("unexpected origin")
            return [
                {
                    "hostname": host,
                    "host": "127.0.0.1",
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": socket.IPPROTO_TCP,
                    "flags": 0,
                }
            ]

        self.enterContext(
            patch.object(aiohttp.TCPConnector, "_resolve_host", resolve_local)
        )
        self.enterContext(
            patch.object(aiohttp.connector, "_SSL_CONTEXT_VERIFIED", client_tls)
        )

        def program(b):
            b = b.decode().replace(
                "        del services",
                "        global SERVICES\n        SERVICES=services",
            )
            b += "\nfrom yomihime_game_link_sdk.services import HttpRequest\nOBS=[]\n"
            return b.replace(
                "        del context, parameters",
                """        scope=await SERVICES.scopes.bind(context)
        assert context.grant_id is None
        op=parameters['action']
        try:
            if op=='begin':
                result=await SERVICES.accounts.begin_login(context)
            elif op=='confirm':
                result=await SERVICES.accounts.confirm_player_login(context,parameters['sid'],parameters['code'])
                result=bool(result)
            elif op=='revoke':
                ref=await SERVICES.accounts.status(context)
                await SERVICES.accounts.revoke(context,ref)
                result='revoked'
            else:
                req=HttpRequest("catalog",parameters.get("path","/profile"))
                response=await (scope.http.fetch(req) if op=="ordinary" else scope.http.fetch_player(req))
                result=response.body.decode()
        except Exception as error:
            result="denied:"+type(error).__name__
        OBS.append((context.actor_id,op,result))
""",
            ).encode()

        transport = AioHttpTransport()
        receiver = await self.prepare(program, config, transport)
        self.addAsyncCleanup(transport.close)
        h = self.core.registry.snapshot().module(OWNER).handlers.capabilities["status"]
        observed = sys.modules[type(h).__module__].OBS

        async def send(who, op, **values):
            ev = _Event("FriendMessage", message="/ygl minimal status")
            ev.sender = who
            ev.conversation = who
            ingress = receiver.make(ev)
            assert ingress.grant_reference is None
            await self.core.invoke_command(
                OWNER, "status", dict(action=op, **values), ingress=ingress
            )
            return next(
                row[2]
                for row in reversed(observed)
                if row[:2] == (ingress.actor_id, op)
            )

        alice = await send("alice", "begin")
        bob = await send("bob", "begin")

        def issue(sid, sub, scope="profile.read"):
            code = secrets.token_urlsafe()
            codes[code] = (sid, sub, scope)
            return code

        a = issue(alice, "remote-alice")
        b = issue(bob, "remote-bob")
        self.assertTrue(
            (await send("bob", "confirm", sid=alice, code=a)).startswith("denied:")
        )
        self.assertTrue(
            (
                await send(
                    "bob",
                    "confirm",
                    sid=bob,
                    code=issue(bob, "remote-bob", "billing.write"),
                )
            ).startswith("denied:")
        )
        self.assertIs(await send("alice", "confirm", sid=alice, code=a), True)
        self.assertIs(await send("bob", "confirm", sid=bob, code=b), True)
        self.assertTrue(
            (await send("alice", "confirm", sid=alice, code=a)).startswith("denied:")
        )
        self.assertEqual(
            json.loads(await send("alice", "read")), {"owned": "remote-alice"}
        )
        self.assertEqual(json.loads(await send("bob", "read")), {"owned": "remote-bob"})
        self.assertTrue((await send("alice", "ordinary")).startswith("denied:"))
        self.assertTrue(
            (await send("alice", "read", path="/identity/remote-bob")).startswith(
                "denied:"
            )
        )
        self.assertEqual(hits, ["remote-alice", "remote-bob"])
        # The SQL grant transaction rejects a second active account for this owner.
        # Its newly staged receipt must be compensated without deleting either live grant.
        conflicting = await send("alice", "begin")
        self.assertTrue(
            (
                await send(
                    "alice",
                    "confirm",
                    sid=conflicting,
                    code=issue(conflicting, "remote-other"),
                )
            ).startswith("denied:")
        )
        state = await self.core.database.executor.run_read(
            lambda unit: [
                row[0]
                for row in unit.execute(
                    "SELECT state FROM secret_receipts WHERE operation_id GLOB ?",
                    (conflicting + ".*",),
                )
            ]
        )
        self.assertEqual(state, ["tombstoned"])
        # A wait for an actual connector precedes serialization of request headers.
        waiting, resume = asyncio.Event(), asyncio.Event()
        connect = aiohttp.TCPConnector.connect

        async def gated(connector, request, *args, **kwargs):
            if request.url.host == "source.example.test":
                waiting.set()
                await resume.wait()
            return await connect(connector, request, *args, **kwargs)

        with patch.object(aiohttp.TCPConnector, "connect", gated):
            pending = asyncio.create_task(send("alice", "read"))
            try:
                await asyncio.wait_for(waiting.wait(), 5)
                self.assertEqual(await send("alice", "revoke"), "revoked")
            finally:
                resume.set()
            self.assertTrue((await pending).startswith("denied:"))
        self.assertEqual(hits, ["remote-alice", "remote-bob"])
        renewed = await send("alice", "begin")
        self.assertIs(
            await send(
                "alice", "confirm", sid=renewed, code=issue(renewed, "remote-alice")
            ),
            True,
        )
        late = True
        task = asyncio.create_task(send("alice", "read"))
        try:
            await asyncio.wait_for(received.wait(), 5)
            self.assertEqual(await send("alice", "revoke"), "revoked")
        finally:
            release.set()
        self.assertTrue((await task).startswith("denied:"))
        self.assertTrue((await send("alice", "read")).startswith("denied:"))
        self.assertEqual(json.loads(await send("bob", "read")), {"owned": "remote-bob"})
        self.assertEqual(
            hits, ["remote-alice", "remote-bob", "remote-alice", "remote-bob"]
        )
