"""Boundary tests for the injected, declaration-scoped HTTP service."""

from __future__ import annotations

import asyncio
import unittest

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.infrastructure.http import (
    CredentialLease,
    SourceHttpError,
    SourceHttpService,
    TransportRequest,
)

from yomihime_game_link_sdk.declarations import SourceDeclaration
from yomihime_game_link_sdk.errors import SourceHttpError as SdkSourceHttpError
from yomihime_game_link_sdk.services import HttpRequest, HttpResponse


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[TransportRequest] = []
        self.response = validate_contract(
            HttpResponse(200, {"Content-Type": "application/json"}, b"ok")
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.error: Exception | None = None

    async def request(self, request: TransportRequest) -> HttpResponse:
        self.calls.append(request)
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.response


class _TwoRequestTransport:
    """Hold the first call briefly and keep the queued call pending."""

    def __init__(self) -> None:
        self.calls: list[TransportRequest] = []
        self.first_started = asyncio.Event()
        self.first_release = asyncio.Event()
        self.second_started = asyncio.Event()
        self.second_gate = asyncio.Event()

    async def request(self, request: TransportRequest) -> HttpResponse:
        self.calls.append(request)
        if len(self.calls) == 1:
            self.first_started.set()
            await self.first_release.wait()
        else:
            self.second_started.set()
            await self.second_gate.wait()
        return validate_contract(HttpResponse(200, {}, b"ok"))


class _CredentialAuthorizer:
    def __init__(self) -> None:
        self.authorizations: list[CredentialLease] = []
        self.invalidated: list[CredentialLease] = []

    async def authorize(self, module_id, declaration, request, deadline):
        lease = CredentialLease(
            f"Bearer lease-{len(self.authorizations) + 1}",
            len(self.authorizations) + 1,
            f"opaque-token-{len(self.authorizations) + 1}",
        )
        self.authorizations.append(lease)
        return lease

    async def invalidate(self, module_id, declaration, lease):
        self.invalidated.append(lease)


class _CredentialResponseTransport:
    def __init__(self, responses: tuple[HttpResponse, ...]) -> None:
        self._credential_channel = object()
        self.responses = list(responses)
        self.calls: list[TransportRequest] = []

    async def request(self, request: TransportRequest) -> HttpResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("unexpected resource replay")
        return self.responses.pop(0)


def _source(**changes: object) -> SourceDeclaration:
    values: dict[str, object] = {
        "source_id": "steam",
        "host": "api.example.test",
        "credential_ref": None,
        "timeout_seconds": 0.05,
        "requests_per_minute": 60,
    }
    values.update(changes)
    return validate_contract(SourceDeclaration(**values))


class SourceHttpServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_type_is_shared_with_the_sdk(self) -> None:
        self.assertIs(SourceHttpError, SdkSourceHttpError)

    async def test_declared_relative_requests_use_fake_transport(self) -> None:
        fake = _FakeTransport()
        service = SourceHttpService((_source(),), fake)

        response = await service.fetch(
            validate_contract(
                HttpRequest("steam", "/v1/items", "POST", (("page", "1"),), b"{}")
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls[0].url, "https://api.example.test/v1/items")
        self.assertIsNone(fake.calls[0].credential_ref)
        self.assertEqual(fake.calls[0].body, b"{}")

    async def test_undeclared_and_unsafe_requests_are_rejected(self) -> None:
        fake = _FakeTransport()
        service = SourceHttpService((_source(),), fake)

        for request in (
            validate_contract(HttpRequest("other", "/v1")),
            validate_contract(HttpRequest("steam", "/v1")),
        ):
            if request.source_id == "steam":
                object.__setattr__(request, "path", "https://evil.example/v1")
            with self.assertRaises(SourceHttpError) as caught:
                await service.fetch(request)
            self.assertIn(
                caught.exception.code, {"source_not_declared", "request_rejected"}
            )
        self.assertEqual(fake.calls, [])

    async def test_quota_is_consumed_by_failure_and_cancellation(self) -> None:
        fake = _FakeTransport()
        fake.block = True
        service = SourceHttpService((_source(requests_per_minute=1),), fake)
        task = asyncio.create_task(
            service.fetch(validate_contract(HttpRequest("steam", "/v1")))
        )
        await fake.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(SourceHttpError) as caught:
            await service.fetch(validate_contract(HttpRequest("steam", "/v1")))
        self.assertEqual(caught.exception.code, "rate_limited")

    async def test_concurrency_limit_and_recovery(self) -> None:
        fake = _FakeTransport()
        fake.block = True
        service = SourceHttpService(
            (_source(),), fake, max_concurrency=1, max_source_concurrency=1
        )
        first = asyncio.create_task(
            service.fetch(validate_contract(HttpRequest("steam", "/first")))
        )
        await fake.started.wait()
        second = asyncio.create_task(
            service.fetch(validate_contract(HttpRequest("steam", "/second")))
        )
        fake.release.set()
        await first
        await second
        self.assertEqual(len(fake.calls), 2)

    async def test_deadline_includes_waiting_for_a_concurrency_slot(self) -> None:
        fake = _TwoRequestTransport()
        service = SourceHttpService(
            (_source(timeout_seconds=0.05),),
            fake,
            max_concurrency=1,
            max_source_concurrency=1,
        )
        first = asyncio.create_task(
            service.fetch(validate_contract(HttpRequest("steam", "/first")))
        )
        await fake.first_started.wait()
        second = asyncio.create_task(
            service.fetch(validate_contract(HttpRequest("steam", "/second")))
        )
        loop = asyncio.get_running_loop()
        loop.call_later(0.02, fake.first_release.set)

        with self.assertRaises(SourceHttpError) as caught:
            await second
        self.assertEqual(caught.exception.code, "timeout")
        await first
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(fake.second_started.is_set())

    async def test_timeout_status_invalid_response_and_size_are_stable(self) -> None:
        fake = _FakeTransport()
        fake.block = True
        service = SourceHttpService((_source(timeout_seconds=0.001),), fake)
        with self.assertRaises(SourceHttpError) as timeout:
            await service.fetch(validate_contract(HttpRequest("steam", "/timeout")))
        self.assertEqual(timeout.exception.code, "timeout")
        self.assertIsNone(timeout.exception.status_code)

        fake.block = False
        service = SourceHttpService((_source(timeout_seconds=0.05),), fake)
        fake.response = validate_contract(
            HttpResponse(503, {}, b"secret upstream body")
        )
        with self.assertRaises(SourceHttpError) as status:
            await service.fetch(validate_contract(HttpRequest("steam", "/status")))
        self.assertEqual(status.exception.code, "upstream_error")
        self.assertNotIn("secret", str(status.exception))

        fake.response = validate_contract(
            HttpResponse(429, {}, b"private quota details")
        )
        with self.assertRaises(SourceHttpError) as upstream_limited:
            await service.fetch(validate_contract(HttpRequest("steam", "/limited")))
        self.assertEqual(upstream_limited.exception.code, "upstream_error")
        self.assertEqual(upstream_limited.exception.status_code, 429)
        self.assertNotIn("private", str(upstream_limited.exception))

        fake.response = validate_contract(
            HttpResponse(404, {}, b"private not found details")
        )
        with self.assertRaises(SourceHttpError) as not_found:
            await service.fetch(validate_contract(HttpRequest("steam", "/missing")))
        self.assertEqual(not_found.exception.code, "upstream_error")
        self.assertEqual(not_found.exception.status_code, 404)
        self.assertNotIn("private", str(not_found.exception))

        fake.response = object()  # type: ignore[assignment]
        with self.assertRaises(SourceHttpError) as invalid:
            await service.fetch(validate_contract(HttpRequest("steam", "/invalid")))
        self.assertEqual(invalid.exception.code, "invalid_response")

        fake.response = validate_contract(HttpResponse(200, {}, b"0123456789"))
        small = SourceHttpService((_source(),), fake, max_response_bytes=4)
        with self.assertRaises(SourceHttpError) as too_large:
            await small.fetch(validate_contract(HttpRequest("steam", "/large")))
        self.assertEqual(too_large.exception.code, "response_too_large")

    async def test_redirect_is_never_followed(self) -> None:
        fake = _FakeTransport()
        fake.response = validate_contract(
            HttpResponse(302, {"Location": "https://evil.example/secret"}, b"")
        )
        service = SourceHttpService((_source(),), fake)
        with self.assertRaises(SourceHttpError) as caught:
            await service.fetch(validate_contract(HttpRequest("steam", "/redirect")))
        self.assertEqual(caught.exception.code, "redirect_disallowed")
        self.assertEqual(len(fake.calls), 1)

    async def test_get_401_refreshes_and_replays_once_with_quota_charge(self) -> None:
        declaration = _source(credential_ref="credential_fflogs")
        transport = _CredentialResponseTransport(
            (
                validate_contract(HttpResponse(401, {}, b"private body")),
                validate_contract(HttpResponse(200, {}, b"ok")),
            )
        )
        authorizer = _CredentialAuthorizer()
        service = SourceHttpService(
            (declaration,),
            transport,
            module_id="sample_pkg/status",
            credential_service=authorizer,
        )

        response = await service.fetch(
            validate_contract(HttpRequest("steam", "/v1/items"))
        )

        self.assertEqual(response.body, b"ok")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(authorizer.authorizations), 2)
        self.assertEqual(len(authorizer.invalidated), 1)
        self.assertEqual(
            [call.headers["Authorization"] for call in transport.calls],
            ["Bearer lease-1", "Bearer lease-2"],
        )

    async def test_post_401_invalidates_but_does_not_replay(self) -> None:
        declaration = _source(credential_ref="credential_fflogs")
        transport = _CredentialResponseTransport(
            (validate_contract(HttpResponse(401, {}, b"private")),)
        )
        authorizer = _CredentialAuthorizer()
        service = SourceHttpService(
            (declaration,),
            transport,
            module_id="sample_pkg/status",
            credential_service=authorizer,
        )

        with self.assertRaises(SourceHttpError) as caught:
            await service.fetch(
                validate_contract(
                    HttpRequest("steam", "/v1/graphql", "POST", body=b"{}")
                )
            )

        self.assertEqual(caught.exception.code, "upstream_error")
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(authorizer.authorizations), 1)
        self.assertEqual(authorizer.invalidated, [authorizer.authorizations[0]])

        transport.responses.append(
            validate_contract(HttpResponse(401, {}, b"still private"))
        )
        with self.assertRaises(SourceHttpError):
            await service.fetch(
                validate_contract(
                    HttpRequest("steam", "/v1/graphql", "POST", body=b"{}")
                )
            )
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(authorizer.authorizations), 2)
        self.assertEqual(len(authorizer.invalidated), 2)

    async def test_credential_declaration_without_internal_authorizer_fails_closed(
        self,
    ) -> None:
        declaration = _source(credential_ref="credential_fflogs")
        fake = _FakeTransport()
        service = SourceHttpService((declaration,), fake, module_id="sample_pkg/status")
        with self.assertRaises(SourceHttpError) as caught:
            await service.fetch(validate_contract(HttpRequest("steam", "/v1/items")))
        self.assertEqual(caught.exception.code, "credentials_unavailable")
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
