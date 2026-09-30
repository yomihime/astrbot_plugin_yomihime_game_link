"""Controlled HTTP service for manifest-declared sources.

The adapter intentionally has no network implementation.  A host supplies a
small transport callable, which makes the policy boundary testable without
making a test fake look like a successful production connection.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import monotonic
from types import MappingProxyType
from typing import Protocol
from urllib.parse import quote_plus, urlencode

from yomihime_sdk.api.services import SourceHttpError

from ..api.manifests import SourceDeclaration
from ..api.services import HttpRequest, HttpResponse, SourceHttp


class HttpTransport(Protocol):
    """The only execution port used by :class:`SourceHttpService`.

    A transport receives a fully policy-checked ``TransportRequest`` and must
    return a public ``HttpResponse``.  It must not follow redirects itself;
    the service rejects redirect responses before they can be treated as data.
    """

    async def request(self, request: "TransportRequest") -> HttpResponse: ...

    async def request_credential_exchange(
        self, request: "CredentialExchangeRequest"
    ) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """Network-neutral request data passed to an injected transport."""

    source_id: str
    host: str
    url: str
    path: str
    method: str
    query: tuple[tuple[str, str], ...]
    body: bytes | None = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    credential_ref: str | None
    timeout_seconds: float
    max_response_bytes: int = 4_194_304
    module_id: str | None = None
    _credential_proof: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))

    def __repr__(self) -> str:
        return (
            "TransportRequest("
            f"module_id={self.module_id!r}, source_id={self.source_id!r}, "
            f"host={self.host!r}, path={self.path!r}, "
            f"method={self.method!r}, credentialed={self.credential_ref is not None}, "
            f"body_bytes={len(self.body) if self.body is not None else 0})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CredentialLease:
    """Core-only short-lived authorization; never returned through the SDK."""

    authorization: str = field(repr=False)
    generation: int
    secret_token: str = field(repr=False)

    def __repr__(self) -> str:
        return f"CredentialLease(generation={self.generation})"


@dataclass(frozen=True, slots=True, repr=False)
class CredentialExchangeRequest:
    """Internal, proof-bearing request for the fixed OAuth token endpoint."""

    module_id: str
    source_id: str
    credential_ref: str
    host: str
    path: str
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    timeout_seconds: float
    max_response_bytes: int
    _proof: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))

    def __repr__(self) -> str:
        return "CredentialExchangeRequest(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _AuthenticatedRequestProof:
    """Per-request capability bound to one transport and exact request fields."""

    channel: object = field(repr=False, compare=False)
    kind: str
    module_id: str
    source_id: str
    credential_ref: str
    host: str
    path: str
    method: str
    query: tuple[tuple[str, str], ...]
    body_digest: bytes = field(repr=False)
    headers_digest: bytes = field(repr=False)
    authorization_digest: bytes = field(repr=False)
    timeout_seconds: float
    max_response_bytes: int

    def __repr__(self) -> str:
        return f"_AuthenticatedRequestProof(kind={self.kind!r})"

    def matches_resource(
        self, channel: object, request: TransportRequest, authorization: str
    ) -> bool:
        return (
            self.channel is channel
            and self.kind == "resource"
            and self.module_id == request.module_id
            and self.source_id == request.source_id
            and self.credential_ref == request.credential_ref
            and self.host == request.host
            and self.path == request.path
            and self.method == request.method
            and self.query == request.query
            and self.body_digest == _body_digest(request.body)
            and self.headers_digest == _headers_digest(request.headers)
            and self.authorization_digest == _text_digest(authorization)
            and self.timeout_seconds == request.timeout_seconds
            and self.max_response_bytes == request.max_response_bytes
        )

    def matches_exchange(
        self, channel: object, request: CredentialExchangeRequest
    ) -> bool:
        authorization = request.headers.get("Authorization")
        return (
            self.channel is channel
            and self.kind == "oauth"
            and self.module_id == request.module_id
            and self.source_id == request.source_id
            and self.credential_ref == request.credential_ref
            and self.host == request.host
            and self.path == request.path
            and self.method == "POST"
            and self.query == ()
            and self.body_digest == _body_digest(request.body)
            and self.headers_digest == _headers_digest(request.headers)
            and type(authorization) is str
            and self.authorization_digest == _text_digest(authorization)
            and self.timeout_seconds == request.timeout_seconds
            and self.max_response_bytes == request.max_response_bytes
        )


def _body_digest(body: bytes | None) -> bytes:
    if body is None:
        return hashlib.sha256(b"\x00").digest()
    return hashlib.sha256(b"\x01" + body).digest()


def _text_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _headers_digest(headers: Mapping[str, str]) -> bytes:
    digest = hashlib.sha256()
    pairs = []
    for key, value in headers.items():
        if type(key) is not str or type(value) is not str:
            raise ValueError("HTTP headers must be text")
        pairs.append((key.lower().encode("ascii"), value.encode("utf-8")))
    for key, value in sorted(pairs):
        digest.update(len(key).to_bytes(4, "big"))
        digest.update(key)
        digest.update(len(value).to_bytes(4, "big"))
        digest.update(value)
    return digest.digest()


def _make_authenticated_request_proof(
    channel: object,
    *,
    kind: str,
    module_id: str,
    source_id: str,
    credential_ref: str,
    host: str,
    path: str,
    method: str,
    query: tuple[tuple[str, str], ...],
    body: bytes | None,
    headers: Mapping[str, str],
    authorization: str,
    timeout_seconds: float,
    max_response_bytes: int,
) -> _AuthenticatedRequestProof:
    if channel is None:
        raise SourceHttpError("credentials_unavailable") from None
    return _AuthenticatedRequestProof(
        channel,
        kind,
        module_id,
        source_id,
        credential_ref,
        host,
        path,
        method,
        query,
        _body_digest(body),
        _headers_digest(headers),
        _text_digest(authorization),
        timeout_seconds,
        max_response_bytes,
    )


def _create_credential_exchange_request(
    channel: object,
    module_id: str,
    source_id: str,
    credential_ref: str,
    host: str,
    path: str,
    client_id: str,
    client_secret: str,
    scope: str | None,
    timeout_seconds: float,
) -> CredentialExchangeRequest:
    """Build the only supported internal Basic-auth request shape."""

    try:
        declaration = SourceDeclaration("oauth_token", host)
        if (
            type(module_id) is not str
            or not module_id.strip()
            or type(source_id) is not str
            or not source_id.strip()
            or type(credential_ref) is not str
            or not credential_ref.startswith("credential_")
        ):
            raise ValueError
        if (
            type(client_id) is not str
            or not client_id
            or len(client_id) > 512
            or type(client_secret) is not str
            or not client_secret
            or len(client_secret) > 512
            or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in client_id)
            or any(
                ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in client_secret
            )
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError
        body_fields = {"grant_type": "client_credentials"}
        if scope is not None:
            if (
                type(scope) is not str
                or not scope
                or len(scope) > 512
                or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in scope)
            ):
                raise ValueError
            body_fields["scope"] = scope
        body = urlencode(body_fields).encode("ascii")
        # RFC 6749 requires form-encoding the client id and secret before HTTP
        # Basic authentication. Neither value is included in the request repr.
        encoded_id = quote_plus(client_id, safe="~")
        encoded_secret = quote_plus(client_secret, safe="~")
        basic = base64.b64encode(
            f"{encoded_id}:{encoded_secret}".encode("utf-8")
        ).decode("ascii")
        HttpRequest("oauth_token", path, "POST", body=body)
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        proof = _make_authenticated_request_proof(
            channel,
            kind="oauth",
            module_id=module_id,
            source_id=source_id,
            credential_ref=credential_ref,
            host=declaration.host,
            path=path,
            method="POST",
            query=(),
            body=body,
            headers=headers,
            authorization=headers["Authorization"],
            timeout_seconds=float(timeout_seconds),
            max_response_bytes=64 * 1024,
        )
        return CredentialExchangeRequest(
            module_id,
            source_id,
            credential_ref,
            declaration.host,
            path,
            body,
            headers,
            float(timeout_seconds),
            64 * 1024,
            proof,
        )
    except Exception:
        raise SourceHttpError("request_rejected") from None


@dataclass(slots=True)
class _SourceState:
    declaration: SourceDeclaration
    semaphore: asyncio.Semaphore
    request_times: deque[float]
    quota_lock: asyncio.Lock


TransportCallable = Callable[[TransportRequest], Awaitable[HttpResponse]]


class SourceCredentialAuthorizer(Protocol):
    async def authorize(
        self,
        module_id: str,
        declaration: SourceDeclaration,
        request: HttpRequest,
        deadline: float,
    ) -> CredentialLease: ...

    async def invalidate(
        self,
        module_id: str,
        declaration: SourceDeclaration,
        lease: CredentialLease,
    ) -> None: ...


class SourceHttpService(SourceHttp):
    """Apply source declarations before handing a request to a transport.

    ``transport`` may expose ``request(TransportRequest)``, ``fetch`` with the
    same signature, or be an async callable.  It is intentionally injected so
    this class never opens a production socket by itself.
    """

    def __init__(
        self,
        declarations: Sequence[SourceDeclaration] | Mapping[str, SourceDeclaration],
        transport: HttpTransport | TransportCallable,
        *,
        max_concurrency: int = 16,
        max_source_concurrency: int = 4,
        max_body_bytes: int = 1_048_576,
        max_response_bytes: int = 4_194_304,
        max_path_length: int = 2_048,
        max_query_items: int = 32,
        max_query_value_length: int = 512,
        clock: Callable[[], float] = monotonic,
        module_id: str | None = None,
        credential_service: SourceCredentialAuthorizer | None = None,
    ) -> None:
        if isinstance(declarations, Mapping):
            if any(type(key) is not str for key in declarations):
                raise TypeError("HTTP declaration keys must be built-in strings")
            values = tuple(declarations.values())
            if any(
                key != value.source_id
                for key, value in declarations.items()
                if isinstance(value, SourceDeclaration)
            ):
                raise ValueError("HTTP declaration keys must match source_id")
        else:
            values = tuple(declarations)
        if any(not isinstance(item, SourceDeclaration) for item in values):
            raise TypeError("HTTP declarations must contain SourceDeclaration values")
        if len({item.source_id for item in values}) != len(values):
            raise ValueError("HTTP source declarations must be unique")
        if not callable(clock):
            raise TypeError("HTTP clock must be callable")
        if module_id is not None and (
            type(module_id) is not str or not module_id.strip()
        ):
            raise ValueError("HTTP module ID must be non-empty text")
        if credential_service is not None and module_id is None:
            raise ValueError("credential HTTP requires a trusted module ID")
        for name, value in (
            ("max_concurrency", max_concurrency),
            ("max_source_concurrency", max_source_concurrency),
            ("max_body_bytes", max_body_bytes),
            ("max_response_bytes", max_response_bytes),
            ("max_path_length", max_path_length),
            ("max_query_items", max_query_items),
            ("max_query_value_length", max_query_value_length),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not any(
            callable(getattr(transport, name, None)) for name in ("request", "fetch")
        ) and not callable(transport):
            raise TypeError(
                "HTTP transport must provide request, fetch, or be callable"
            )

        self._transport = transport
        self._clock = clock
        self._module_id = module_id
        self._credential_service = credential_service
        self._credential_channel = getattr(transport, "_credential_channel", None)
        self._max_body_bytes = max_body_bytes
        self._max_response_bytes = max_response_bytes
        self._max_path_length = max_path_length
        self._max_query_items = max_query_items
        self._max_query_value_length = max_query_value_length
        self._global_semaphore = asyncio.Semaphore(max_concurrency)
        self._sources = {
            declaration.source_id: _SourceState(
                declaration,
                asyncio.Semaphore(max_source_concurrency),
                deque(),
                asyncio.Lock(),
            )
            for declaration in values
        }

    async def close_credentials(self) -> None:
        """Clear this HTTP binding's token cache without closing shared transport."""

        service = self._credential_service
        close = getattr(service, "close", None)
        if callable(close):
            await close()

    async def fetch(self, request: HttpRequest) -> HttpResponse:
        """Fetch one request after enforcing the declared source policy."""

        checked = self._validate_request(request)
        source = self._sources.get(checked.source_id)
        if source is None:
            raise SourceHttpError("source_not_declared") from None
        self._validate_limits(checked)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(source.declaration.timeout_seconds)

        # Acquire in a fixed order.  Each wait shares the request deadline;
        # cancellation while waiting releases only resources already held and
        # does not consume a quota reservation.
        global_acquired = False
        source_acquired = False

        try:
            await self._wait_for_deadline(self._global_semaphore.acquire(), deadline)
            global_acquired = True
            await self._wait_for_deadline(source.semaphore.acquire(), deadline)
            source_acquired = True
            await self._reserve_quota(source, deadline)
            lease = await self._credential_lease(source.declaration, checked, deadline)
            result = await self._perform_resource_request(
                source.declaration, checked, lease, deadline
            )
            response = self._validate_response(result)
            self._ensure_remaining(deadline)
            if response.status_code == 401 and lease is not None:
                await self._invalidate_credential(source.declaration, lease, deadline)
                if checked.method == "GET":
                    await self._reserve_quota(source, deadline)
                    refreshed = await self._credential_lease(
                        source.declaration, checked, deadline
                    )
                    response = self._validate_response(
                        await self._perform_resource_request(
                            source.declaration, checked, refreshed, deadline
                        )
                    )
                    if response.status_code == 401:
                        await self._invalidate_credential(
                            source.declaration, refreshed, deadline
                        )
                    self._ensure_remaining(deadline)
            if 300 <= response.status_code <= 399:
                raise SourceHttpError(
                    "redirect_disallowed", status_code=response.status_code
                ) from None
            if 400 <= response.status_code <= 499:
                raise SourceHttpError(
                    "upstream_error", status_code=response.status_code
                ) from None
            if 500 <= response.status_code <= 599:
                raise SourceHttpError(
                    "upstream_error", status_code=response.status_code
                ) from None
            return response
        finally:
            if source_acquired:
                # A quota entry is deliberately retained after failure or
                # cancellation.  It represents an attempted upstream request
                # and cannot be returned to another caller's quota.
                source.semaphore.release()
            if global_acquired:
                self._global_semaphore.release()

    async def _credential_lease(
        self,
        declaration: SourceDeclaration,
        request: HttpRequest,
        deadline: float,
    ) -> CredentialLease | None:
        if declaration.credential_ref is None:
            return None
        service = self._credential_service
        module_id = self._module_id
        if service is None or module_id is None:
            raise SourceHttpError("credentials_unavailable") from None
        try:
            return await self._wait_for_deadline(
                service.authorize(module_id, declaration, request, deadline), deadline
            )
        except asyncio.CancelledError:
            raise
        except SourceHttpError:
            raise
        except Exception:
            raise SourceHttpError("credentials_unavailable") from None

    async def _invalidate_credential(
        self,
        declaration: SourceDeclaration,
        lease: CredentialLease,
        deadline: float,
    ) -> None:
        service = self._credential_service
        module_id = self._module_id
        if service is None or module_id is None:
            raise SourceHttpError("credentials_unavailable") from None
        try:
            await self._wait_for_deadline(
                service.invalidate(module_id, declaration, lease), deadline
            )
        except asyncio.CancelledError:
            raise
        except SourceHttpError:
            raise
        except Exception:
            raise SourceHttpError("credentials_unavailable") from None

    async def _perform_resource_request(
        self,
        declaration: SourceDeclaration,
        checked: HttpRequest,
        lease: CredentialLease | None,
        deadline: float,
    ) -> HttpResponse:
        headers = dict(checked.headers)
        proof = None
        if lease is not None:
            module_id = self._module_id
            if declaration.credential_ref is None or module_id is None:
                raise SourceHttpError("credentials_unavailable") from None
            headers["Authorization"] = lease.authorization
            proof = _make_authenticated_request_proof(
                self._credential_channel,
                kind="resource",
                module_id=module_id,
                source_id=declaration.source_id,
                credential_ref=declaration.credential_ref,
                host=declaration.host,
                path=checked.path,
                method=checked.method,
                query=checked.query,
                body=checked.body,
                headers=headers,
                authorization=lease.authorization,
                timeout_seconds=float(declaration.timeout_seconds),
                max_response_bytes=self._max_response_bytes,
            )
        transport_request = TransportRequest(
            declaration.source_id,
            declaration.host,
            f"https://{declaration.host}{checked.path}",
            checked.path,
            checked.method,
            checked.query,
            checked.body,
            headers,
            declaration.credential_ref,
            float(declaration.timeout_seconds),
            self._max_response_bytes,
            self._module_id,
            proof,
        )
        try:
            return self._validate_response(
                await self._wait_for_deadline(
                    self._invoke_transport(transport_request), deadline
                )
            )
        except asyncio.CancelledError:
            raise
        except SourceHttpError:
            raise
        except Exception:
            raise SourceHttpError("transport_failed") from None

    async def _invoke_transport(self, request: TransportRequest) -> HttpResponse:
        method = getattr(self._transport, "request", None)
        if not callable(method):
            method = getattr(self._transport, "fetch", None)
        if not callable(method):
            method = self._transport
        value = method(request)
        if inspect.isawaitable(value):
            return await value
        raise SourceHttpError("invalid_response") from None

    async def _reserve_quota(self, source: _SourceState, deadline: float) -> None:
        now = float(self._clock())
        if not now == now or now in (float("inf"), float("-inf")):
            raise SourceHttpError("request_rejected") from None
        await self._wait_for_deadline(source.quota_lock.acquire(), deadline)
        try:
            cutoff = now - 60.0
            while source.request_times and source.request_times[0] <= cutoff:
                source.request_times.popleft()
            if len(source.request_times) >= source.declaration.requests_per_minute:
                raise SourceHttpError("rate_limited") from None
            source.request_times.append(now)
        finally:
            source.quota_lock.release()

    @staticmethod
    async def _wait_for_deadline(awaitable, deadline: float):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise SourceHttpError("timeout") from None
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.TimeoutError:
            raise SourceHttpError("timeout") from None

    @staticmethod
    def _ensure_remaining(deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise SourceHttpError("timeout") from None

    def _validate_request(self, request: HttpRequest) -> HttpRequest:
        if not isinstance(request, HttpRequest):
            raise SourceHttpError("request_rejected") from None
        try:
            return HttpRequest(
                request.source_id,
                request.path,
                request.method,
                request.query,
                request.body,
                request.headers,
            )
        except Exception:
            raise SourceHttpError("request_rejected") from None

    def _validate_limits(self, request: HttpRequest) -> None:
        if len(request.path) > self._max_path_length:
            raise SourceHttpError("request_too_large") from None
        if len(request.query) > self._max_query_items or any(
            len(key) > self._max_query_value_length
            or len(value) > self._max_query_value_length
            for key, value in request.query
        ):
            raise SourceHttpError("request_too_large") from None
        if request.body is not None and len(request.body) > self._max_body_bytes:
            raise SourceHttpError("request_too_large") from None

    def _validate_response(self, response: object) -> HttpResponse:
        if not isinstance(response, HttpResponse):
            raise SourceHttpError("invalid_response") from None
        try:
            checked = HttpResponse(
                response.status_code,
                response.headers,
                response.body,
            )
        except Exception:
            raise SourceHttpError("invalid_response") from None
        if len(checked.body) > self._max_response_bytes:
            raise SourceHttpError("response_too_large") from None
        return checked


# Names used by assemblers can describe either the policy service or its role.
SourceHttpAdapter = SourceHttpService
ControlledSourceHttp = SourceHttpService
SourceHttp = SourceHttpService

__all__ = [
    "ControlledSourceHttp",
    "HttpTransport",
    "SourceHttpAdapter",
    "SourceHttpError",
    "SourceHttpService",
    "SourceHttp",
    "TransportRequest",
]
