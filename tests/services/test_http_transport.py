"""Production HTTP transport contract tests with an isolated fake session."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

from ygl_test_subject.api.manifests import SourceDeclaration
from ygl_test_subject.api.services import HttpRequest
from ygl_test_subject.infrastructure.http import (
    CredentialLease,
    SourceHttpError,
    SourceHttpService,
    TransportRequest,
    _create_credential_exchange_request,
)
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport


class _Stream:
    def __init__(self, chunks: tuple[bytes, ...], *, gate: asyncio.Event | None = None):
        self.chunks = chunks
        self.gate = gate
        self.started = asyncio.Event()

    async def iter_chunked(self, size: int):
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        for chunk in self.chunks:
            yield chunk


class _HeaderText(str):
    """Mimic aiohttp multidict strings accepted by the HTTP response boundary."""


class _ResponseContext:
    def __init__(self, *, chunks: tuple[bytes, ...] = (b"ok",), length=None, gate=None):
        self.response = SimpleNamespace(
            content_length=length,
            content=_Stream(chunks, gate=gate),
            headers={
                "Content-Type": "application/json",
                "Set-Cookie": "session=secret",
                "WWW-Authenticate": "Bearer secret",
                "X-Trace": "safe",
            },
            status=200,
        )
        self.exited = False

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True


class _Session:
    def __init__(self, context: _ResponseContext):
        self.context = context
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.close_count = 0

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.context

    async def close(self):
        self.close_count += 1


class _CredentialAuthorizer:
    async def authorize(self, module_id, declaration, request, deadline):
        return CredentialLease("Bearer safe-token", 1, "opaque-token")

    async def invalidate(self, module_id, declaration, lease):
        return None


def _request(
    *,
    path: str = "/api/search",
    query: tuple[tuple[str, str], ...] = (),
    credential_ref: str | None = None,
    max_response_bytes: int = 32,
) -> TransportRequest:
    return TransportRequest(
        "xivapi",
        "api.example.test",
        f"https://api.example.test{path}",
        path,
        "GET",
        query,
        None,
        {},
        credential_ref,
        2.5,
        max_response_bytes,
    )


class AioHttpRealSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_session_constructs_reuses_and_closes_without_network(self):
        import aiohttp

        transport = AioHttpTransport()
        session = None
        try:
            session = await transport._get_session()
            self.assertIsInstance(session, aiohttp.ClientSession)
            self.assertIs(await transport._get_session(), session)
            self.assertIsInstance(session.cookie_jar, aiohttp.DummyCookieJar)
            self.assertFalse(session.trust_env)
            self.assertFalse(session.auto_decompress)
            self.assertEqual(session.connector.limit, 16)
        finally:
            await transport.close()
        self.assertTrue(session.closed)
        await transport.close()
        with self.assertRaises(SourceHttpError) as rejected:
            await transport._get_session()
        self.assertEqual(rejected.exception.code, "transport_failed")


class AioHttpTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = _ResponseContext()
        self.session = _Session(self.context)
        self.transport = AioHttpTransport()
        self.transport._session = self.session  # type: ignore[assignment]
        self.context.response.headers = {
            _HeaderText(key): _HeaderText(value)
            for key, value in self.context.response.headers.items()
        }

    async def asyncTearDown(self) -> None:
        await self.transport.close()

    async def test_query_is_encoded_once_and_request_policy_is_explicit(self) -> None:
        value = 'Name~"White ore 50% +#&' + chr(38081) + chr(30719)
        response = await self.transport.request(
            _request(query=(("query", value), ("limit", "2")))
        )

        args, kwargs = self.session.calls[0]
        url = args[1]
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.host, "api.example.test")
        self.assertEqual(
            parse_qsl(urlsplit(str(url)).query, keep_blank_values=True),
            [("query", value), ("limit", "2")],
        )
        encoded_query = urlsplit(str(url)).query
        self.assertIn("50%25", encoded_query)
        self.assertNotIn("%2525", encoded_query)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertIs(kwargs["ssl"], True)
        self.assertIsNone(kwargs["proxy"])
        self.assertEqual(kwargs["timeout"].total, 2.5)
        self.assertEqual(response.body, b"ok")
        self.assertEqual(
            response.headers, {"Content-Type": "application/json", "X-Trace": "safe"}
        )
        self.assertTrue(all(type(value) is str for value in response.headers))

    async def test_path_injection_is_rejected_before_any_io(self) -> None:
        for path in ("/safe?next=https://evil.example", "/safe#fragment"):
            with self.subTest(path=path), self.assertRaises(SourceHttpError) as caught:
                await self.transport.request(_request(path=path))
            self.assertEqual(caught.exception.code, "request_rejected")
        forged_host = _request()
        object.__setattr__(forged_host, "host", "evil.example/path")
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(forged_host)
        self.assertEqual(caught.exception.code, "request_rejected")

        invalid_host = _request()
        object.__setattr__(invalid_host, "host", "a^b.example")
        object.__setattr__(invalid_host, "url", "https://a^b.example/api/search")
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(invalid_host)
        self.assertEqual(caught.exception.code, "request_rejected")
        self.assertEqual(self.session.calls, [])

    async def test_session_creation_errors_are_sanitized(self) -> None:
        async def fail_session():
            raise ValueError("private proxy configuration")

        self.transport._get_session = fail_session  # type: ignore[method-assign]
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request())
        self.assertEqual(caught.exception.code, "transport_failed")
        self.assertNotIn("private", str(caught.exception))

    async def test_source_http_errors_are_preserved(self) -> None:
        error = SourceHttpError("invalid_response")

        class _ErrorSession:
            def request(self, *args, **kwargs):
                raise error

            async def close(self):
                return None

        self.transport._session = _ErrorSession()  # type: ignore[assignment]
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request())
        self.assertIs(caught.exception, error)

    async def test_lazy_session_disables_implicit_proxy_and_decompression(
        self,
    ) -> None:
        transport = AioHttpTransport()
        session = _Session(_ResponseContext())
        with patch(
            "ygl_test_subject.infrastructure.http_transport.aiohttp.ClientSession",
            return_value=session,
        ) as create_session:
            await transport._get_session()
            kwargs = create_session.call_args.kwargs
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["auto_decompress"])
            self.assertEqual(kwargs["connector"]._limit, 16)
            self.assertEqual(type(kwargs["cookie_jar"]).__name__, "DummyCookieJar")
            await kwargs["connector"].close()
        await transport.close()
        self.assertEqual(session.close_count, 1)

    async def test_explicit_proxy_is_passed_without_becoming_transport_output(
        self,
    ) -> None:
        proxy_url = "http://proxy-user:proxy-password@127.0.0.1:7890"
        transport = AioHttpTransport(proxy_url)
        session = _Session(_ResponseContext())
        transport._session = session  # type: ignore[assignment]

        await transport.request(_request())

        kwargs = session.calls[0][1]
        self.assertEqual(kwargs["proxy"], proxy_url)
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
        self.assertNotIn("proxy-password", repr(transport))
        await transport.close()
        with self.assertRaisesRegex(ValueError, "invalid") as caught:
            AioHttpTransport("http://user:proxy-password@example.test/path")
        self.assertNotIn("proxy-password", str(caught.exception))

    async def test_credential_ref_fails_closed_without_creating_a_session(self) -> None:
        transport = AioHttpTransport()
        with self.assertRaises(SourceHttpError) as caught:
            await transport.request(_request(credential_ref="credential_xiv"))
        self.assertEqual(caught.exception.code, "credentials_unavailable")
        self.assertIsNone(transport._session)

    async def test_oauth_exchange_uses_fixed_endpoint_and_redacted_dto(self) -> None:
        request = _create_credential_exchange_request(
            self.transport._credential_channel,
            "sample_pkg/status",
            "fflogs",
            "credential_fflogs",
            "oauth.example.test",
            "/oauth/token",
            "client-id",
            "private-client-secret",
            None,
            4.0,
        )

        await self.transport.request_credential_exchange(request)

        args, kwargs = self.session.calls[0]
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1].host, "oauth.example.test")
        self.assertEqual(args[1].path, "/oauth/token")
        self.assertEqual(
            kwargs["headers"]["Content-Type"], "application/x-www-form-urlencoded"
        )
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Basic "))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertIs(kwargs["ssl"], True)
        self.assertEqual(kwargs["timeout"].total, 4.0)
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
        self.assertNotIn("private-client-secret", repr(request))
        self.assertNotIn("Basic ", repr(request))

    async def test_oauth_proof_rejects_mutation_and_cross_transport_reuse(self) -> None:
        request = _create_credential_exchange_request(
            self.transport._credential_channel,
            "sample_pkg/status",
            "fflogs",
            "credential_fflogs",
            "oauth.example.test",
            "/oauth/token",
            "client-id",
            "private-client-secret",
            None,
            4.0,
        )
        object.__setattr__(request, "host", "other.example.test")
        with self.assertRaises(SourceHttpError) as mutated:
            await self.transport.request_credential_exchange(request)
        self.assertEqual(mutated.exception.code, "request_rejected")
        self.assertEqual(self.session.calls, [])

        other_transport = AioHttpTransport()
        other_session = _Session(_ResponseContext())
        other_transport._session = other_session  # type: ignore[assignment]
        other_request = _create_credential_exchange_request(
            self.transport._credential_channel,
            "sample_pkg/status",
            "fflogs",
            "credential_fflogs",
            "oauth.example.test",
            "/oauth/token",
            "client-id",
            "private-client-secret",
            None,
            4.0,
        )
        try:
            with self.assertRaises(SourceHttpError) as crossed:
                await other_transport.request_credential_exchange(other_request)
            self.assertEqual(crossed.exception.code, "request_rejected")
            self.assertEqual(other_session.calls, [])
        finally:
            await other_transport.close()

    async def test_internal_source_service_is_the_only_resource_proof_issuer(
        self,
    ) -> None:
        declaration = SourceDeclaration(
            "fflogs", "api.example.test", "credential_fflogs"
        )
        service = SourceHttpService(
            (declaration,),
            self.transport,
            module_id="sample_pkg/status",
            credential_service=_CredentialAuthorizer(),
        )

        response = await service.fetch(HttpRequest("fflogs", "/api/v2/client"))

        self.assertEqual(response.status_code, 200)
        args, kwargs = self.session.calls[0]
        self.assertEqual(args[1].host, "api.example.test")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer safe-token")

        forged = _request(credential_ref="credential_fflogs")
        object.__setattr__(forged, "headers", {"Authorization": "Bearer safe-token"})
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(forged)
        self.assertEqual(caught.exception.code, "credentials_unavailable")

    async def test_upstream_exception_details_are_sanitized(self) -> None:
        class _RaisingSession:
            def request(self, *args, **kwargs):
                raise RuntimeError(
                    "https://user:password@api.example.test/?token=private"
                )

            async def close(self):
                return None

        self.transport._session = _RaisingSession()  # type: ignore[assignment]
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request())
        self.assertEqual(caught.exception.code, "transport_failed")
        self.assertNotIn("password", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    async def test_client_timeout_has_a_stable_timeout_code(self) -> None:
        class _TimedOutSession:
            def request(self, *args, **kwargs):
                raise TimeoutError("proxy-password and target query are private")

            async def close(self):
                return None

        self.transport._session = _TimedOutSession()  # type: ignore[assignment]
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request())
        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("proxy-password", str(caught.exception))

    async def test_streamed_response_is_bounded_and_context_closes(self) -> None:
        self.context = _ResponseContext(chunks=(b"1234", b"5678"))
        self.session = _Session(self.context)
        self.transport._session = self.session  # type: ignore[assignment]
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request(max_response_bytes=6))
        self.assertEqual(caught.exception.code, "response_too_large")
        self.assertTrue(self.context.exited)

    async def test_content_length_is_rejected_without_reading_body(self) -> None:
        self.context = _ResponseContext(chunks=(b"ignored",), length=33)
        self.session = _Session(self.context)
        self.transport._session = self.session  # type: ignore[assignment]
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request(max_response_bytes=32))
        self.assertEqual(caught.exception.code, "response_too_large")
        self.assertFalse(self.context.response.content.started.is_set())
        self.assertTrue(self.context.exited)

    async def test_compressed_response_is_rejected_without_unbounded_expansion(
        self,
    ) -> None:
        self.context.response.headers["Content-Encoding"] = "gzip"
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request())
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertFalse(self.context.response.content.started.is_set())
        self.assertTrue(self.context.exited)

    async def test_cancellation_unwinds_response_and_close_is_idempotent(self) -> None:
        gate = asyncio.Event()
        self.context = _ResponseContext(gate=gate)
        self.session = _Session(self.context)
        self.transport._session = self.session  # type: ignore[assignment]
        task = asyncio.create_task(self.transport.request(_request()))
        await self.context.response.content.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.context.exited)

        await self.transport.close()
        await self.transport.close()
        self.assertTrue(self.transport.closed)
        self.assertEqual(self.session.close_count, 1)
        with self.assertRaises(SourceHttpError) as caught:
            await self.transport.request(_request())
        self.assertEqual(caught.exception.code, "transport_failed")


if __name__ == "__main__":
    unittest.main()
