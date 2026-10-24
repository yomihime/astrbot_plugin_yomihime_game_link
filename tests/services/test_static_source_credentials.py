"""Static credential use with real encrypted SQLite material and transport proof."""

import json
import unittest
from dataclasses import replace

from ygl_test_subject.core.contracts.services import (
    ConfigFieldUpdate,
    PersistedConfigPatch,
)
from ygl_test_subject.core.contracts.storage import SecretTarget
from ygl_test_subject.infrastructure.http import SourceHttpService
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
from ygl_test_subject.services.source_credentials import (
    CredentialInjection,
    SourceCredentialPolicy,
    SourceCredentialService,
    bind_source_credential_declarations,
)

from yomihime_game_link_sdk import (
    ConfigUpdateMode,
    HttpRequest,
    HttpResponse,
    SourceDeclaration,
    SourceHttpError,
)

from . import test_source_credentials as fixture_module


class StaticCredentialsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture_module.SourceCredentialTests()
        await self.fixture.asyncSetUp()
        self.token = "static-key-private-123"
        await self.activate(self.token)
        self.calls = []
        self.transport = AioHttpTransport()

        async def send(method, url, body, headers, timeout_seconds, maximum):
            self.calls.append((method, str(url), body, headers))
            return HttpResponse(
                200,
                {"X-Debug": self.token},
                json.dumps({"echo": self.token, "id": "123"}).encode(),
            )

        self.transport._send = send
        self.policy = SourceCredentialPolicy(
            fixture_module.MODULE_ID,
            fixture_module.SOURCE_ID,
            fixture_module.ALIAS,
            fixture_module.RESOURCE_HOST,
            (fixture_module.RESOURCE_PATH,),
            kind="static",
            injections=(
                CredentialInjection(
                    fixture_module.RESOURCE_PATH, "POST", "header", "token"
                ),
                CredentialInjection(
                    fixture_module.RESOURCE_PATH, "POST", "json", "accessToken"
                ),
                CredentialInjection(
                    fixture_module.RESOURCE_PATH, "POST", "json", "mySteamId", "subject"
                ),
            ),
        )

    async def asyncTearDown(self):
        await self.transport.close()
        await self.fixture.asyncTearDown()

    async def activate(self, token):
        fixture = self.fixture
        revision = fixture.snapshot.revision
        operation = f"static-v{revision}"
        target = SecretTarget(
            fixture_module.PRINCIPAL_ID, fixture_module.MODULE_ID, fixture_module.ALIAS
        )
        payload = json.dumps({"schema": 2, "token": token, "subject": "123"}).encode()
        receipt = await fixture.store.stage(
            payload,
            target=target,
            operation_id=operation,
            expected_config_revision=revision,
        )
        claimed = await fixture.store.claim_for_config(
            receipt,
            target=target,
            operation_id=operation,
            expected_config_revision=revision,
            expected_ledger_revision=receipt.ledger_revision,
        )
        patch = PersistedConfigPatch(
            revision,
            (
                ConfigFieldUpdate(
                    fixture_module.ALIAS, ConfigUpdateMode.REPLACE, receipt=receipt
                ),
            ),
            (fixture.field,),
            operation,
            fixture.snapshot.target,
        )
        fixture.snapshot = await fixture.config.update(fixture.snapshot.target, patch)
        await fixture.store.finalize_active(
            claimed, metadata_revision=fixture.snapshot.revision
        )

    def http(self, policy=None):
        fixture = self.fixture
        policy = policy or self.policy
        declarations = bind_source_credential_declarations(
            fixture_module.MODULE_ID, (fixture.source,), (fixture.field,), (policy,)
        )
        credentials = SourceCredentialService(
            fixture_module.MODULE_ID,
            declarations,
            (fixture.field,),
            (policy,),
            fixture.config,
            fixture.store,
            self.transport,
            config_principal_id=fixture_module.PRINCIPAL_ID,
        )
        return SourceHttpService(
            declarations,
            self.transport,
            module_id=fixture_module.MODULE_ID,
            credential_service=credentials,
        )

    async def test_header_json_injection_redaction_rotation_and_private_proof(self):
        http = self.http()
        response = await http.fetch(
            HttpRequest(
                fixture_module.SOURCE_ID,
                fixture_module.RESOURCE_PATH,
                "POST",
                body=b'{"target":"other-player"}',
            )
        )
        self.assertNotIn(self.token, repr(response))
        body = json.loads(self.calls[-1][2])
        self.assertEqual(
            body,
            {"target": "other-player", "accessToken": self.token, "mySteamId": "123"},
        )
        self.assertEqual(self.calls[-1][3]["token"], self.token)
        self.assertEqual(json.loads(response.body)["id"], "123")
        await self.activate("rotated-private-key")
        await http.fetch(
            HttpRequest(fixture_module.SOURCE_ID, fixture_module.RESOURCE_PATH, "POST")
        )
        self.assertEqual(
            json.loads(self.calls[-1][2])["accessToken"], "rotated-private-key"
        )

    async def test_json_equivalent_secret_spellings_are_redacted_after_decoding(self):
        async def send(method, url, body, headers, timeout_seconds, maximum):
            # These are distinct valid JSON spellings of the injected secret.
            escaped = "".join(f"\\u{ord(char):04x}" for char in self.token)
            mixed = self.token[:5] + "".join(
                f"\\u{ord(char):04X}" for char in self.token[5:]
            )
            return HttpResponse(
                200,
                {},
                (
                    '{"nested":[{"value":"prefix-'
                    + escaped
                    + '-suffix"},"'
                    + mixed
                    + '"],"'
                    + escaped
                    + '":"key echo","business":{"id":"123","score":13}}'
                ).encode(),
            )

        self.transport._send = send
        response = await self.http().fetch(
            HttpRequest(fixture_module.SOURCE_ID, fixture_module.RESOURCE_PATH, "POST")
        )
        decoded = json.loads(response.body)
        self.assertNotIn(self.token, repr(decoded))
        self.assertEqual(
            decoded["nested"], [{"value": "prefix-[redacted]-suffix"}, "[redacted]"]
        )
        self.assertEqual(decoded["[redacted]"], "key echo")
        self.assertEqual(decoded["business"], {"id": "123", "score": 13})

    async def test_wrong_path_method_body_collision_and_header_collision_reject_before_wire(
        self,
    ):
        http = self.http()
        for request in (
            HttpRequest(fixture_module.SOURCE_ID, "/other", "POST"),
            HttpRequest(fixture_module.SOURCE_ID, fixture_module.RESOURCE_PATH, "GET"),
            HttpRequest(
                fixture_module.SOURCE_ID,
                fixture_module.RESOURCE_PATH,
                "POST",
                body=b'{"accessToken":"fake"}',
            ),
        ):
            with self.assertRaises(SourceHttpError):
                await http.fetch(request)
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            bind_source_credential_declarations(
                "other_pkg/module",
                (self.fixture.source,),
                (self.fixture.field,),
                (self.policy,),
            )

    async def test_optional_configured_but_unreadable_never_degrades_anonymous(self):
        http = self.http(replace(self.policy, optional=True))
        self.fixture.key_provider.key = None
        with self.assertRaises(SourceHttpError) as caught:
            await http.fetch(
                HttpRequest(
                    fixture_module.SOURCE_ID, fixture_module.RESOURCE_PATH, "POST"
                )
            )
        self.assertEqual(caught.exception.code, "credentials_unavailable")
        self.assertEqual(self.calls, [])

    async def test_authorization_source_is_not_available_to_module_http(self):
        http = SourceHttpService(
            (SourceDeclaration("login", "auth.example.test", authorization_only=True),),
            self.transport,
        )
        with self.assertRaises(SourceHttpError):
            await http.fetch(HttpRequest("login", "/begin", "POST"))
        self.assertEqual(self.calls, [])
