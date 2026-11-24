import importlib
import unittest
from types import SimpleNamespace

from tests.host import test_private_core_integration as support
from tests.host.test_astrbot_runtime import _Event


def manifest(document):
    module = document["modules"][0]
    cap = module["capabilities"][0]
    cap.update(
        invocation_policy="command_only",
        privacy_floor="owner",
        invocation_origins=["command"],
        required_sources=["catalog"],
    )
    module["sources"] = [{"source_id": "catalog", "host": "source.example.test"}]


def probe(body):
    s = body.decode().replace(
        "        del services", "        global SERVICES\n        SERVICES=services"
    )
    s += "\nfrom ._api_v2.services import HttpRequest\nOBS=[]\n"
    s = s.replace(
        "        del context, parameters",
        """        scope=await SERVICES.scopes.bind(context)
        row={"has_grant":context.grant_id is not None,"kind":context.conversation_kind.value,"actor":context.actor_id}
        ref=await SERVICES.accounts.status(context)
        row["has_status"]=ref is not None
        sid=await SERVICES.accounts.begin_login(context)
        row["began"]=bool(sid)
        try:
            await scope.http.fetch_player(HttpRequest("catalog","/profile"))
        except Exception as exc:
            row["denied"]=type(exc).__name__
        OBS.append(row)
""",
    )
    return s.encode()


class Ingress(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.PrivateCoreIntegrationTest.asyncSetUp
    stop = support.PrivateCoreIntegrationTest.stop
    make_dlc = support.PrivateCoreIntegrationTest.make_dlc

    async def test_events_preserve_identity_pending_login_and_default_denial(self):
        def no_issuer(document):
            manifest(document)
            document["modules"][0]["capabilities"][0]["required_sources"] = []

        installation, _ = await self.make_dlc(
            "playerprobe", source_transform=probe, manifest_transform=no_issuer
        )
        result = await self.manager.replace(installation)
        self.assertEqual(result["state"], "active")
        sample = self.manager.packages["playerprobe"].instances["playerprobe/demo"]
        mod = importlib.import_module(type(sample._delegate).__module__)
        for who in ("alice", "bob"):
            event = _Event(
                "FriendMessage",
                context=SimpleNamespace(calls=[], error=None),
                message="/ygl playerprobe status",
            )
            event.sender = who
            event.conversation = who
            await self.runtime.handle_event(event)
        self.assertEqual(len(mod.OBS), 2, mod.OBS)
        for who, row in zip(("alice", "bob"), mod.OBS):
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
