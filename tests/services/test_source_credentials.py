"""Credential ownership, OAuth exchange, and disk-manifest boundary tests."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from ygl_test_subject.core.contracts.services import (
    ConfigFieldUpdate,
    PersistedConfigPatch,
)
from ygl_test_subject.core.contracts.storage import SecretTarget
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.extensions.disk_manifest import ManifestError, parse_manifest
from ygl_test_subject.infrastructure.http import SourceHttpService
from ygl_test_subject.infrastructure.key_provider import SecretKeyUnavailable
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.services.source_credentials import (
    SourceCredentialPolicy,
    SourceCredentialService,
    bind_source_credential_declarations,
)

from yomihime_game_link_sdk.declarations import (
    ConfigField,
    ConfigUpdateMode,
    SourceDeclaration,
)
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.services import (
    ConfigSnapshot,
    ConfigTarget,
    HttpRequest,
    HttpResponse,
)
from yomihime_game_link_sdk.storage import (
    SecretMetadata,
    SecretMetadataState,
    SecretRef,
)

MODULE_ID = "sample_pkg/status"
PRINCIPAL_ID = "operator-a"
SOURCE_ID = "fflogs"
ALIAS = "credential_fflogs"
RESOURCE_HOST = "api.example.test"
RESOURCE_PATH = "/api/v2/client"
TOKEN_HOST = "oauth.example.test"
TOKEN_PATH = "/oauth/token"
CLIENT_ID = "public-client-id"
CLIENT_SECRET = "private-client-secret-value"
_MISSING = object()


class _MutableKeyProvider:
    def __init__(self, key: bytes | None = b"k" * 32) -> None:
        self.key = key

    def get_key(self) -> bytes:
        if self.key is None:
            raise SecretKeyUnavailable()
        return self.key


class _CredentialTransport:
    def __init__(self) -> None:
        self._credential_channel = object()
        self.exchanges = []
        self.resources = []
        self.exchange_started = asyncio.Event()
        self.exchange_gate: asyncio.Event | None = None
        self.exchange_error: Exception | None = None
        self.expires_in: object = 3600
        self.resource_response = validate_contract(HttpResponse(200, {}, b"ok"))

    async def request_credential_exchange(self, request):
        self.exchanges.append(request)
        self.exchange_started.set()
        if self.exchange_gate is not None:
            await self.exchange_gate.wait()
        if self.exchange_error is not None:
            raise self.exchange_error
        token = f"access-token-{len(self.exchanges)}"
        token_payload = {"access_token": token, "token_type": "Bearer"}
        if self.expires_in is not _MISSING:
            token_payload["expires_in"] = self.expires_in
        return validate_contract(
            HttpResponse(
                200,
                {"Content-Type": "application/json"},
                json.dumps(token_payload).encode(),
            )
        )

    async def request(self, request):
        self.resources.append(request)
        return self.resource_response


def _document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_id": "sample_pkg",
        "package_version": "1.0.0",
        "contract_version": "2.0",
        "author": "Example",
        "license": "MIT",
        "source": "https://example.invalid/project",
        "modules": [
            {
                "module_id": "status",
                "route": "status",
                "category": "game",
                "factory_entry": "sample.factory:Factory",
                "module_version": "1.0.0",
                "capabilities": [
                    {
                        "capability_id": "lookup",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                        "invocation_policy": "natural_language_allowed",
                        "effect": "read_only",
                    }
                ],
                "config_fields": [{"name": ALIAS, "sensitive": True}],
                "sources": [{"source_id": SOURCE_ID, "host": RESOURCE_HOST}],
            }
        ],
    }


class SourceCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = SQLiteDatabase(self.root / "state.sqlite3")
        self.config = SQLiteConfigRepository(self.database)
        self.key_provider = _MutableKeyProvider()
        self.codec = AESGCMSecretCodec(self.key_provider)
        self.store = SQLiteSecretStore(
            self.database, self.root / "secrets", codec=self.codec
        )
        self.field = validate_contract(
            ConfigField(ALIAS, sensitive=True, required=True)
        )
        self.source = validate_contract(SourceDeclaration(SOURCE_ID, RESOURCE_HOST))
        self.policy = SourceCredentialPolicy(
            MODULE_ID,
            SOURCE_ID,
            ALIAS,
            RESOURCE_HOST,
            (RESOURCE_PATH,),
            TOKEN_HOST,
            TOKEN_PATH,
        )
        self.transport = _CredentialTransport()
        self.snapshot = await self._activate_credentials(
            operation_id="credential-v1",
            expected_revision=1,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        self.effective = bind_source_credential_declarations(
            MODULE_ID, (self.source,), (self.field,), (self.policy,)
        )

    async def asyncTearDown(self) -> None:
        await self.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def _activate_credentials(
        self,
        *,
        operation_id: str,
        expected_revision: int,
        client_id: str,
        client_secret: str,
    ):
        target = validate_contract(ConfigTarget(PRINCIPAL_ID, MODULE_ID))
        secret_target = SecretTarget(PRINCIPAL_ID, MODULE_ID, ALIAS)
        payload = json.dumps(
            {"schema": 1, "client_id": client_id, "client_secret": client_secret}
        ).encode()
        receipt = await self.store.stage(
            payload,
            target=secret_target,
            operation_id=operation_id,
            expected_config_revision=expected_revision,
        )
        claimed = await self.store.claim_for_config(
            receipt,
            target=secret_target,
            operation_id=operation_id,
            expected_config_revision=expected_revision,
            expected_ledger_revision=receipt.ledger_revision,
        )
        patch = PersistedConfigPatch(
            expected_revision,
            (ConfigFieldUpdate(ALIAS, ConfigUpdateMode.REPLACE, receipt=receipt),),
            (self.field,),
            operation_id,
            target,
        )
        snapshot = await self.config.update(target, patch)
        await self.store.finalize_active(claimed, metadata_revision=snapshot.revision)
        return snapshot

    def _credential_service(self, *, clock=None) -> SourceCredentialService:
        options = {} if clock is None else {"clock": clock}
        return SourceCredentialService(
            MODULE_ID,
            self.effective,
            (self.field,),
            (self.policy,),
            self.config,
            self.store,
            self.transport,
            config_principal_id=PRINCIPAL_ID,
            **options,
        )

    def _source_http(self, credential_service=None) -> SourceHttpService:
        if credential_service is None:
            credential_service = self._credential_service()
        return SourceHttpService(
            self.effective,
            self.transport,
            module_id=MODULE_ID,
            credential_service=credential_service,
        )

    async def test_disk_manifest_cannot_select_alias_but_host_policy_can_bind_it(self):
        package = parse_manifest(json.dumps(_document()).encode())
        declaration = package.modules[0].sources[0]
        self.assertIsNone(declaration.credential_ref)
        effective = bind_source_credential_declarations(
            MODULE_ID,
            package.modules[0].sources,
            package.modules[0].config_fields,
            (self.policy,),
        )
        self.assertEqual(effective[0].credential_ref, ALIAS)

        malicious = _document()
        malicious["modules"][0]["sources"][0]["credential_ref"] = ALIAS  # type: ignore[index]
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(malicious).encode())

        wrong = validate_contract(
            SourceDeclaration(SOURCE_ID, RESOURCE_HOST, "credential_other")
        )
        with self.assertRaises(ValueError):
            bind_source_credential_declarations(
                MODULE_ID, (wrong,), (self.field,), (self.policy,)
            )
        without_policy = bind_source_credential_declarations(
            MODULE_ID, (self.source,), (self.field,), ()
        )
        self.assertIsNone(without_policy[0].credential_ref)
        with self.assertRaises(ValueError):
            bind_source_credential_declarations(MODULE_ID, (wrong,), (self.field,), ())

    async def test_real_sqlite_metadata_drives_basic_exchange_and_resource_bearer(self):
        service = self._credential_service()
        response = await self._source_http(service).fetch(
            validate_contract(
                HttpRequest(SOURCE_ID, RESOURCE_PATH, "GET", (("name", "Mina"),))
            )
        )
        self.assertEqual(response.body, b"ok")
        self.assertEqual(len(self.transport.exchanges), 1)
        exchange = self.transport.exchanges[0]
        self.assertEqual(exchange.host, TOKEN_HOST)
        self.assertEqual(exchange.path, TOKEN_PATH)
        self.assertEqual(
            exchange.headers["Content-Type"], "application/x-www-form-urlencoded"
        )
        basic = base64.b64decode(exchange.headers["Authorization"].split(" ", 1)[1])
        self.assertEqual(basic.decode(), f"{CLIENT_ID}:{CLIENT_SECRET}")
        self.assertEqual(
            parse_qs(exchange.body.decode()), {"grant_type": ["client_credentials"]}
        )

        resource = self.transport.resources[0]
        self.assertEqual(resource.host, RESOURCE_HOST)
        self.assertTrue(
            resource.headers["Authorization"].startswith("Bearer access-token-")
        )
        self.assertNotIn(CLIENT_SECRET, repr(exchange))
        self.assertNotIn(CLIENT_SECRET, str(exchange))
        self.assertNotIn(CLIENT_SECRET, repr(resource))
        self.assertNotIn(CLIENT_SECRET, str(resource))
        self.assertNotIn("access-token", repr(service))
        secret_blob = next((self.root / "secrets").glob("*.blob")).read_bytes()
        self.assertNotIn(CLIENT_SECRET.encode(), secret_blob)
        self.assertEqual(len(self.snapshot.secret_metadata), 1)
        self.assertIs(
            self.snapshot.secret_metadata[0].state, SecretMetadataState.ACTIVE
        )
        self.assertNotIn(ALIAS, self.snapshot.values)

    async def test_cached_token_still_requires_current_key_and_intact_payload(self):
        service = self._credential_service()
        http = self._source_http(service)
        request = validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
        await http.fetch(request)
        self.assertEqual(len(self.transport.resources), 1)

        self.key_provider.key = b"w" * 32
        with self.assertRaises(SourceHttpError) as wrong_key:
            await http.fetch(request)
        self.assertEqual(
            getattr(wrong_key.exception, "code", None), "credentials_unavailable"
        )
        self.assertEqual(len(self.transport.resources), 1)

        self.key_provider.key = None
        with self.assertRaises(SourceHttpError) as missing_key:
            await http.fetch(request)
        self.assertEqual(
            getattr(missing_key.exception, "code", None), "credentials_unavailable"
        )
        self.assertEqual(len(self.transport.resources), 1)

    async def test_ref_rotation_invalidates_cached_token(self):
        service = self._credential_service()
        http = self._source_http(service)
        request = validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
        await http.fetch(request)
        self.assertEqual(len(self.transport.exchanges), 1)

        await self._activate_credentials(
            operation_id="credential-v2",
            expected_revision=self.snapshot.revision,
            client_id="rotated-client-id",
            client_secret="rotated-client-secret",
        )
        await http.fetch(request)
        self.assertEqual(len(self.transport.exchanges), 2)
        second_basic = base64.b64decode(
            self.transport.exchanges[1].headers["Authorization"].split(" ", 1)[1]
        ).decode()
        self.assertEqual(second_basic, "rotated-client-id:rotated-client-secret")

    async def test_restart_recovers_active_secret_ref_from_sqlite_metadata(self):
        await self.database.executor.close(timeout=2)
        reopened_database = SQLiteDatabase(self.root / "state.sqlite3")
        reopened_config = SQLiteConfigRepository(reopened_database)
        reopened_store = SQLiteSecretStore(
            reopened_database, self.root / "secrets", codec=self.codec
        )
        try:
            snapshot = await reopened_config.current(
                validate_contract(ConfigTarget(PRINCIPAL_ID, MODULE_ID))
            )
            self.assertEqual(len(snapshot.secret_metadata), 1)
            self.assertEqual(snapshot.secret_metadata[0].secret_ref.field, ALIAS)
            service = SourceCredentialService(
                MODULE_ID,
                self.effective,
                (self.field,),
                (self.policy,),
                reopened_config,
                reopened_store,
                self.transport,
                config_principal_id=PRINCIPAL_ID,
            )
            http = SourceHttpService(
                self.effective,
                self.transport,
                module_id=MODULE_ID,
                credential_service=service,
            )
            await http.fetch(validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH)))
            self.assertEqual(len(self.transport.exchanges), 1)
            self.assertEqual(len(self.transport.resources), 1)
        finally:
            await reopened_database.executor.close(timeout=2)

    async def test_concurrent_token_requests_are_single_flight_and_expire_early(self):
        now = [100.0]
        self.transport.exchange_gate = asyncio.Event()
        service = self._credential_service(clock=lambda: now[0])
        deadline = asyncio.get_running_loop().time() + 5
        request = validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
        first = asyncio.create_task(
            service.authorize(MODULE_ID, self.effective[0], request, deadline)
        )
        await self.transport.exchange_started.wait()
        second = asyncio.create_task(
            service.authorize(MODULE_ID, self.effective[0], request, deadline)
        )
        await asyncio.sleep(0)
        self.transport.exchange_gate.set()
        first_lease, second_lease = await asyncio.gather(first, second)
        self.assertEqual(len(self.transport.exchanges), 1)
        self.assertEqual(first_lease.authorization, second_lease.authorization)

        now[0] = 4000.0
        await service.authorize(
            MODULE_ID,
            self.effective[0],
            request,
            asyncio.get_running_loop().time() + 5,
        )
        self.assertEqual(len(self.transport.exchanges), 2)

    async def test_long_provider_ttl_is_accepted_but_local_cache_is_capped(self):
        self.transport.expires_in = 31104000
        now = [100.0]
        service = self._credential_service(clock=lambda: now[0])

        lease = await service.authorize(
            MODULE_ID,
            self.effective[0],
            validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH)),
            asyncio.get_running_loop().time() + 5,
        )

        self.assertTrue(lease.authorization.startswith("Bearer access-token-"))
        self.assertEqual(len(self.transport.exchanges), 1)
        cache_residence = service._states[SOURCE_ID].expires_at - now[0]
        self.assertGreater(cache_residence, 0)
        self.assertLessEqual(cache_residence, 86400)

    async def test_nonpositive_nonfinite_or_missing_provider_ttl_fails_closed(self):
        for ttl in (-1, 0, float("nan"), float("inf"), _MISSING):
            with self.subTest(expires_in=ttl):
                self.transport.expires_in = ttl
                service = self._credential_service()
                with self.assertRaises(SourceHttpError) as caught:
                    await self._source_http(service).fetch(
                        validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
                    )
                self.assertEqual(caught.exception.code, "invalid_response")
                self.assertEqual(len(self.transport.resources), 0)

    async def test_cancelled_oauth_exchange_propagates_without_fallback(self):
        self.transport.exchange_gate = asyncio.Event()
        service = self._credential_service()
        task = asyncio.create_task(
            service.authorize(
                MODULE_ID,
                self.effective[0],
                validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH)),
                asyncio.get_running_loop().time() + 5,
            )
        )
        await self.transport.exchange_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.transport.resources, [])

    async def test_http_binding_close_clears_cache_and_rejects_future_auth(self):
        credential_service = self._credential_service()
        http = self._source_http(credential_service)
        await http.fetch(validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH)))
        self.assertEqual(len(self.transport.resources), 1)

        await http.close_credentials()

        with self.assertRaises(SourceHttpError) as caught:
            await http.fetch(validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH)))
        self.assertEqual(caught.exception.code, "credentials_unavailable")
        self.assertEqual(len(self.transport.resources), 1)

    async def test_oauth_timeout_is_stable_and_does_not_fall_back_to_anonymous(self):
        self.transport.exchange_error = TimeoutError(
            "private proxy and credential details"
        )
        service = self._credential_service()
        with self.assertRaises(SourceHttpError) as caught:
            await self._source_http(service).fetch(
                validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
            )
        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.transport.resources, [])

    async def test_oauth_preserves_closed_codes_and_actual_status_without_body(self):
        for status, code in (
            (401, "upstream_error"),
            (403, "upstream_error"),
            (429, "rate_limited"),
            (503, "upstream_error"),
            (200, "invalid_response"),
        ):
            with self.subTest(status=status):

                async def exchange(_request):
                    return validate_contract(
                        HttpResponse(status, {}, b"private-upstream-body")
                    )

                self.transport.request_credential_exchange = exchange
                with self.assertRaises(SourceHttpError) as caught:
                    await self._source_http().fetch(
                        validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(
                    caught.exception.status_code, status if status != 200 else None
                )
                self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.transport.resources, [])

    async def test_oauth_transport_and_oversize_keep_closed_errors(self):
        for failure, code in (
            (RuntimeError("synthetic-private-transport"), "transport_failed"),
            (SourceHttpError("response_too_large"), "response_too_large"),
            (SourceHttpError("invalid_response"), "invalid_response"),
        ):
            with self.subTest(code=code):

                async def exchange(_request):
                    raise failure

                self.transport.request_credential_exchange = exchange
                with self.assertRaises(SourceHttpError) as caught:
                    await self._source_http().fetch(
                        validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private", str(caught.exception))

        async def oversized(_request):
            return validate_contract(HttpResponse(200, {}, b"x" * (64 * 1024 + 1)))

        self.transport.request_credential_exchange = oversized
        with self.assertRaises(SourceHttpError) as caught:
            await self._source_http().fetch(
                validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
            )
        self.assertEqual(caught.exception.code, "response_too_large")
        self.assertEqual(self.transport.resources, [])

    async def test_policy_requires_unique_sensitive_alias_and_exact_resource_path(self):
        duplicate = SourceCredentialPolicy(
            MODULE_ID,
            "other-source",
            ALIAS,
            RESOURCE_HOST,
            (RESOURCE_PATH,),
            TOKEN_HOST,
            TOKEN_PATH,
        )
        other = validate_contract(SourceDeclaration("other-source", RESOURCE_HOST))
        with self.assertRaises(ValueError):
            bind_source_credential_declarations(
                MODULE_ID,
                (self.source, other),
                (self.field,),
                (self.policy, duplicate),
            )

        service = self._credential_service()
        with self.assertRaises(SourceHttpError) as denied:
            await service.authorize(
                MODULE_ID,
                self.effective[0],
                validate_contract(HttpRequest(SOURCE_ID, "/api/v2/other")),
                asyncio.get_running_loop().time() + 5,
            )
        self.assertEqual(
            getattr(denied.exception, "code", None), "credentials_unavailable"
        )
        self.assertEqual(self.transport.exchanges, [])

    async def test_config_secret_ref_cannot_cross_module_or_principal(self):
        foreign_ref = validate_contract(
            SecretRef(
                "secret_foreign",
                "operator-b",
                "other_pkg/status",
                ALIAS,
                "foreign-operation",
            )
        )

        class _ForeignConfig:
            async def current(self, target):
                return validate_contract(
                    ConfigSnapshot(
                        2,
                        {},
                        (
                            validate_contract(
                                SecretMetadata(
                                    ALIAS, foreign_ref, 2, SecretMetadataState.ACTIVE
                                )
                            ),
                        ),
                        target,
                    )
                )

        class _NeverReadStore:
            def __init__(self):
                self.reads = 0

            async def read(self, secret_ref, *, owner):
                self.reads += 1
                raise AssertionError("foreign ref reached SecretStore.read")

        store = _NeverReadStore()
        service = SourceCredentialService(
            MODULE_ID,
            self.effective,
            (self.field,),
            (self.policy,),
            _ForeignConfig(),
            store,
            self.transport,
            config_principal_id=PRINCIPAL_ID,
        )
        with self.assertRaises(SourceHttpError) as caught:
            await service.authorize(
                MODULE_ID,
                self.effective[0],
                validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH)),
                asyncio.get_running_loop().time() + 5,
            )
        self.assertEqual(caught.exception.code, "credentials_unavailable")
        self.assertEqual(store.reads, 0)

    async def test_late_401_for_old_lease_does_not_invalidate_new_token(self):
        service = self._credential_service()
        request = validate_contract(HttpRequest(SOURCE_ID, RESOURCE_PATH))
        deadline = asyncio.get_running_loop().time() + 5
        old_lease = await service.authorize(
            MODULE_ID, self.effective[0], request, deadline
        )
        await service.invalidate(MODULE_ID, self.effective[0], old_lease)
        new_lease = await service.authorize(
            MODULE_ID, self.effective[0], request, deadline
        )
        self.assertNotEqual(old_lease, new_lease)
        await service.invalidate(MODULE_ID, self.effective[0], old_lease)
        cached = await service.authorize(
            MODULE_ID, self.effective[0], request, deadline
        )
        self.assertEqual(cached, new_lease)
        self.assertEqual(len(self.transport.exchanges), 2)


if __name__ == "__main__":
    unittest.main()
