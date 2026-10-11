"""SDK sample source checks; injected ports do not prove Core authority."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import get_type_hints

import yomihime_game_link_sdk as sdk
from examples.offline_sample.module import read_tool_message


class MessageSampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_helper_binds_exact_input_and_reads_only_public_port(self):
        invocation = object()
        message = sdk.MessageContext("INPUT_CANARY", "REF_CANARY")
        calls = []

        async def read():
            calls.append("read")
            return message

        async def bind(value):
            self.assertIs(value, invocation)
            calls.append("bind")
            return SimpleNamespace(message=SimpleNamespace(read=read))

        services = SimpleNamespace(scopes=SimpleNamespace(bind=bind))
        self.assertIs(await read_tool_message(services, invocation), message)
        self.assertEqual(calls, ["bind", "read"])
        self.assertNotIn(message.text, repr(message))
        self.assertNotIn(message.event_ref, repr(message))

    async def test_helper_propagates_public_unavailability_and_cancel(self):
        import asyncio

        for error in (sdk.ServiceUnavailable(), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                async def read():
                    raise error

                async def bind(_value):
                    return SimpleNamespace(message=SimpleNamespace(read=read))

                services = SimpleNamespace(scopes=SimpleNamespace(bind=bind))
                with self.assertRaises(type(error)) as raised:
                    await read_tool_message(services, object())
                self.assertIs(raised.exception, error)

    def test_helper_public_annotations_resolve(self):
        self.assertEqual(
            get_type_hints(read_tool_message),
            {"services": sdk.ModuleServices, "invocation": sdk.InvocationView,
             "return": sdk.MessageContext},
        )
