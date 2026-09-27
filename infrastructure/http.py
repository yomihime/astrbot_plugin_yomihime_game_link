"""Controlled HTTP service for manifest-declared sources.

The adapter intentionally has no network implementation.  A host supplies a
small transport callable, which makes the policy boundary testable without
making a test fake look like a successful production connection.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from types import MappingProxyType
from typing import Protocol

from ..api.manifests import SourceDeclaration
from ..api.services import HttpRequest, HttpResponse, SourceHttp


class SourceHttpError(RuntimeError):
    """Stable, sanitized failure from the source HTTP boundary."""

    _MESSAGES = {
        "source_not_declared": "HTTP source is not declared",
        "request_rejected": "HTTP request is outside the declared source policy",
        "request_too_large": "HTTP request exceeds the source policy limit",
        "response_too_large": "HTTP response exceeds the source policy limit",
        "rate_limited": "HTTP source quota exceeded",
        "concurrency_limited": "HTTP source concurrency limit exceeded",
        "timeout": "HTTP source request timed out",
        "cancelled": "HTTP source request was cancelled",
        "transport_failed": "HTTP source transport failed",
        "invalid_response": "HTTP source returned an invalid response",
        "upstream_error": "HTTP source returned an upstream error",
        "redirect_disallowed": "HTTP source redirect is not allowed",
    }

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        if code not in self._MESSAGES:
            code = "transport_failed"
        self.code = code
        self.status_code = status_code
        super().__init__(self._MESSAGES[code])


class HttpTransport(Protocol):
    """The only execution port used by :class:`SourceHttpService`.

    A transport receives a fully policy-checked ``TransportRequest`` and must
    return a public ``HttpResponse``.  It must not follow redirects itself;
    the service rejects redirect responses before they can be treated as data.
    """

    async def request(self, request: "TransportRequest") -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """Network-neutral request data passed to an injected transport."""

    source_id: str
    host: str
    url: str
    path: str
    method: str
    query: tuple[tuple[str, str], ...]
    body: bytes | None
    headers: Mapping[str, str]
    credential_ref: str | None
    timeout_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(slots=True)
class _SourceState:
    declaration: SourceDeclaration
    semaphore: asyncio.Semaphore
    request_times: deque[float]
    quota_lock: asyncio.Lock


TransportCallable = Callable[[TransportRequest], Awaitable[HttpResponse]]


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
            transport_request = TransportRequest(
                source.declaration.source_id,
                source.declaration.host,
                f"https://{source.declaration.host}{checked.path}",
                checked.path,
                checked.method,
                checked.query,
                checked.body,
                checked.headers,
                source.declaration.credential_ref,
                float(source.declaration.timeout_seconds),
            )
            try:
                result = await self._wait_for_deadline(
                    self._invoke_transport(transport_request), deadline
                )
            except asyncio.CancelledError:
                raise
            except SourceHttpError:
                raise
            except Exception:
                raise SourceHttpError("transport_failed") from None
            response = self._validate_response(result)
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
