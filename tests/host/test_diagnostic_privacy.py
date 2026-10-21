"""Synthetic secrets must not cross the real Core -> Host logger path."""

import unittest
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.diagnostics import forward_diagnostic
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.services.diagnostics import (
    BoundDiagnostics,
    DiagnosticPolicy,
    DiagnosticStore,
)

from yomihime_game_link_sdk import DiagnosticDeclaration, ParameterError


def declaration(properties):
    return DiagnosticDeclaration(
        "PrivacyProbe",
        {"type": "object", "properties": properties, "additionalProperties": False},
    )


class DiagnosticPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def emit(self, properties, payload):
        messages = []
        self.messages = messages
        logger = SimpleNamespace(debug=messages.append)
        store = DiagnosticStore(lambda event: forward_diagnostic(event, logger=logger))
        self.addCleanup(store.close)
        view = SimpleNamespace(
            invocation_id="a" * 32, module_id="sample/sample", module_epoch=1
        )

        async def current():
            pass

        event = validate_contract(declaration(properties))
        port = BoundDiagnostics(
            view,
            (event,),
            DiagnosticPolicy(frozenset({event.event_id})),
            store,
            current,
        )
        await port.emit(event.event_id, payload)
        return messages, store.snapshot()

    def test_credential_fields_rejected_when_registering_nested_schema(self):
        for key in (
            "api_key",
            "clientSecret",
            "master_key",
            "private_key",
            "headers",
            "refresh_token",
            "oauth_client_secret",
            "token",
            "credential",
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_contract(
                    declaration(
                        {
                            "nested": {
                                "type": "array",
                                "maxItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        key: {"type": "string", "maxLength": 512}
                                    },
                                },
                            }
                        }
                    )
                )

    def test_secret_status_names_cannot_hide_unrestricted_values(self):
        for key in ("client_secret_configured", "authorization_status"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_contract(
                    declaration({key: {"type": "string", "maxLength": 512}})
                )

    async def test_safe_status_fields_reach_logger_without_secret_values(self):
        messages, snapshot = await self.emit(
            {
                "client_secret_configured": {"type": "boolean"},
                "authorization_status": {
                    "type": "string",
                    "maxLength": 16,
                    "enum": ["ready", "unavailable"],
                },
            },
            {"client_secret_configured": True, "authorization_status": "ready"},
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(snapshot[0]["payload"]["authorization_status"], "ready")

    async def test_recognizable_auth_material_in_neutral_text_never_reaches_sink(self):
        values = (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljX3NpZ25hdHVyZQ",
            "https://example.invalid/query?api_key=synthetic-only",
            "-----BEGIN PRIVATE KEY----- synthetic-only",
            "Basic c3ludGhldGljOm9ubHk=",
            "Cookie: session=synthetic-only",
        )
        for value in values:
            with self.subTest(format=value.split(" ")[0]):
                with self.assertRaises(ParameterError):
                    await self.emit(
                        {"detail": {"type": "string", "maxLength": 512}},
                        {"detail": value},
                    )
                self.assertEqual(self.messages, [])
