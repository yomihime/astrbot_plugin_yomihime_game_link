"""Official QQ identities are opaque strings, never numeric account numbers."""

import unittest
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.runtime import _AstrBotMessageIngress

from yomihime_game_link_sdk.subscriptions import ConversationKind


class OfficialQQIdentityTests(unittest.TestCase):
    def test_ingress_preserves_uid_group_id_and_platform_instance(self):
        uid = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
        group_id = "F0E1D2C3B4A5968778695A4B3C2D1E0F"
        for instance in ("official_a", "official_b"):
            for message_type, session_id, kind in (
                ("FriendMessage", uid, ConversationKind.DIRECT),
                ("GroupMessage", group_id, ConversationKind.GROUP),
            ):
                with self.subTest(instance=instance, message_type=message_type):
                    event = SimpleNamespace(
                        get_platform_id=lambda: instance,
                        get_sender_id=lambda: uid,
                        get_session_id=lambda: session_id,
                        get_message_type=lambda: message_type,
                    )
                    self.assertEqual(
                        _AstrBotMessageIngress._read_event_facts(event),
                        (instance, uid, session_id, kind),
                    )
