"""Production HTTPS transport for declaration-scoped source requests."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qsl

import aiohttp
from yarl import URL

from yomihime_sdk.api.services import SourceHttpError

from ..api.manifests import SourceDeclaration
from ..api.services import HttpRequest, HttpResponse
from .http import (
    CredentialExchangeRequest,
    TransportRequest,
    _AuthenticatedRequestProof,
)

_RESPONSE_SECRET_MARKERS = (
    "auth",
    "authorization",
    "authentication",
    "cookie",
    "bearer",
    "credential",
    "password",
    "token",
)
_CHUNK_SIZE = 64 * 1024
_BEARER_TOKEN = re.compile(r"Bearer [A-Za-z0-9._~+/=-]+\Z")
_BASIC_TOKEN = re.compile(r"Basic [A-Za-z0-9+/]+={0,2}\Z")


class AioHttpTransport:
    """Own one reusable aiohttp session and bound each response while streaming.

    The session is created lazily on the active event loop. SDK request headers
    never carry credentials. Core-authenticated requests require the private
    proof attached by SourceHttpService; token exchange uses a separate
    proof-bearing DTO with a fixed HTTPS endpoint and grant shape.
    """

    def __init__(self, proxy_url: str | None = None) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        self._credential_channel = object()
        self._closed = False
        self._proxy_url = self._validate_proxy_url(proxy_url)

    @property
    def closed(self) -> bool:
        """Whether this transport has completed its local close operation."""

        return self._closed

    async def request(self, request: TransportRequest) -> HttpResponse:
        declaration, checked, authorization = self._validate_request(request)
        try:
            try:
                url = URL.build(
                    scheme="https", host=declaration.host, path=checked.path
                ).with_query(checked.query)
            except Exception:
                raise SourceHttpError("request_rejected") from None
            headers = dict(checked.headers)
            if authorization is not None:
                headers["Authorization"] = authorization
            return await self._send(
                checked.method,
                url,
                checked.body,
                headers,
                float(request.timeout_seconds),
                request.max_response_bytes,
            )
        except asyncio.CancelledError:
            raise
        except SourceHttpError:
            raise
        except asyncio.TimeoutError:
            raise SourceHttpError("timeout") from None
        except Exception:
            # Do not propagate aiohttp exception text: it can include a URL or
            # upstream-controlled detail. SourceHttp exposes only stable codes.
            raise SourceHttpError("transport_failed") from None

    async def request_credential_exchange(
        self, request: CredentialExchangeRequest
    ) -> HttpResponse:
        """Send only a factory-issued client_credentials exchange request."""

        host, path, headers, body, timeout_seconds, maximum = (
            self._validate_credential_exchange(request)
        )
        try:
            try:
                url = URL.build(scheme="https", host=host, path=path)
            except Exception:
                raise SourceHttpError("request_rejected") from None
            return await self._send(
                "POST", url, body, headers, timeout_seconds, maximum
            )
        except asyncio.CancelledError:
            raise
        except SourceHttpError:
            raise
        except asyncio.TimeoutError:
            raise SourceHttpError("timeout") from None
        except Exception:
            raise SourceHttpError("transport_failed") from None

    async def _send(
        self,
        method: str,
        url: URL,
        body: bytes | None,
        request_headers: dict[str, str],
        timeout_seconds: float,
        maximum: int,
    ) -> HttpResponse:
        session = await self._get_session()
        headers = dict(request_headers)
        headers["Accept-Encoding"] = "identity"
        async with session.request(
            method,
            url,
            data=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=float(timeout_seconds)),
            allow_redirects=False,
            proxy=self._proxy_url,
            ssl=True,
        ) as response:
            content_encoding = response.headers.get("Content-Encoding", "")
            if content_encoding.strip().lower() not in {"", "identity"}:
                raise SourceHttpError("invalid_response") from None
            if (
                response.content_length is not None
                and response.content_length > maximum
            ):
                raise SourceHttpError("response_too_large") from None
            body_buffer = bytearray()
            async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
                body_buffer.extend(chunk)
                if len(body_buffer) > maximum:
                    raise SourceHttpError("response_too_large") from None

            response_headers = {
                str(key): str(value)
                for key, value in response.headers.items()
                if not any(marker in key.lower() for marker in _RESPONSE_SECRET_MARKERS)
            }
            return HttpResponse(response.status, response_headers, bytes(body_buffer))

    async def close(self) -> None:
        """Close the owned session once; safe to call during repeated shutdown."""

        async with self._session_lock:
            if self._closed:
                return
            self._closed = True
            session = self._session
            self._session = None
        if session is not None:
            await session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._closed:
                raise SourceHttpError("transport_failed") from None
            if self._session is None:
                self._session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(limit=16),
                    cookie_jar=aiohttp.DummyCookieJar(),
                    trust_env=False,
                    auto_decompress=False,
                )
            return self._session

    @staticmethod
    def _validate_proxy_url(proxy_url: str | None) -> str | None:
        if proxy_url is None:
            return None
        try:
            if (
                type(proxy_url) is not str
                or not proxy_url
                or "?" in proxy_url
                or "#" in proxy_url
                or any(character.isspace() for character in proxy_url)
                or any(
                    ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                    for character in proxy_url
                )
            ):
                raise ValueError
            parsed = URL(proxy_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.host
                or parsed.path not in {"", "/"}
                or parsed.query_string
                or parsed.fragment
            ):
                raise ValueError
            # Accessing port also validates malformed numeric ports.
            _ = parsed.port
            return str(parsed)
        except Exception:
            # Proxy configuration may contain credentials; never echo it.
            raise ValueError("HTTP proxy URL is invalid") from None

    def _validate_request(
        self, request: object
    ) -> tuple[SourceDeclaration, HttpRequest, str | None]:
        if not isinstance(request, TransportRequest):
            raise SourceHttpError("request_rejected") from None
        try:
            declaration = SourceDeclaration(
                request.source_id,
                request.host,
                request.credential_ref,
                request.timeout_seconds,
                1,
            )
            if (
                isinstance(request.max_response_bytes, bool)
                or not isinstance(request.max_response_bytes, int)
                or request.max_response_bytes < 1
            ):
                raise ValueError

            validation_headers = dict(request.headers)
            authorization = None
            auth_keys = [
                key
                for key in validation_headers
                if type(key) is str and key.lower() == "authorization"
            ]
            if declaration.credential_ref is not None:
                if not isinstance(
                    request._credential_proof, _AuthenticatedRequestProof
                ):
                    raise SourceHttpError("credentials_unavailable")
                if len(auth_keys) != 1:
                    raise ValueError
                auth_key = auth_keys[0]
                authorization = validation_headers.pop(auth_key)
                if type(authorization) is not str or not _BEARER_TOKEN.fullmatch(
                    authorization
                ):
                    raise ValueError
                if not request._credential_proof.matches_resource(
                    self._credential_channel, request, authorization
                ):
                    raise ValueError
            elif auth_keys:
                raise ValueError
            elif request._credential_proof is not None:
                raise ValueError
            checked = HttpRequest(
                request.source_id,
                request.path,
                request.method,
                request.query,
                request.body,
                validation_headers,
            )
            expected_url = f"https://{declaration.host}{checked.path}"
            if request.url != expected_url:
                raise ValueError
            return declaration, checked, authorization
        except SourceHttpError:
            raise
        except Exception:
            raise SourceHttpError("request_rejected") from None

    def _validate_credential_exchange(
        self, request: object
    ) -> tuple[str, str, dict[str, str], bytes, float, int]:
        if not isinstance(request, CredentialExchangeRequest) or not isinstance(
            request._proof, _AuthenticatedRequestProof
        ):
            raise SourceHttpError("request_rejected") from None
        try:
            declaration = SourceDeclaration("oauth_token", request.host)
            checked = HttpRequest(
                "oauth_token", request.path, "POST", body=request.body
            )
            headers = dict(request.headers)
            if set(headers) != {"Authorization", "Content-Type", "Accept"}:
                raise ValueError
            authorization = headers.get("Authorization")
            if (
                type(authorization) is not str
                or not _BASIC_TOKEN.fullmatch(authorization)
                or headers.get("Content-Type") != "application/x-www-form-urlencoded"
                or headers.get("Accept") != "application/json"
            ):
                raise ValueError
            if not request._proof.matches_exchange(self._credential_channel, request):
                raise ValueError
            body_text = checked.body.decode("ascii") if checked.body is not None else ""
            form_fields = parse_qsl(
                body_text, keep_blank_values=True, strict_parsing=True
            )
            if (
                len(form_fields) not in {1, 2}
                or form_fields[0] != ("grant_type", "client_credentials")
                or any(key not in {"grant_type", "scope"} for key, _ in form_fields)
                or len({key for key, _ in form_fields}) != len(form_fields)
            ):
                raise ValueError
            if (
                isinstance(request.timeout_seconds, bool)
                or not isinstance(request.timeout_seconds, (int, float))
                or request.timeout_seconds <= 0
                or isinstance(request.max_response_bytes, bool)
                or not isinstance(request.max_response_bytes, int)
                or not 1 <= request.max_response_bytes <= 64 * 1024
            ):
                raise ValueError
            return (
                declaration.host,
                checked.path,
                headers,
                checked.body or b"",
                float(request.timeout_seconds),
                request.max_response_bytes,
            )
        except SourceHttpError:
            raise
        except Exception:
            raise SourceHttpError("request_rejected") from None


__all__ = ["AioHttpTransport"]
