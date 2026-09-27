"""Invocation-bound cache access.

The repository owns persistence and optimistic concurrency.  This module owns
the smaller, security-sensitive boundary between an invocation and an owner
scope: the scope is derived from the invocation, and an authorised grant is
looked up again for every operation so a revoked or rotated grant cannot keep
using an old cache partition.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Callable, TypeVar

from ..api.contexts import InvocationView
from ..api.services import CacheAccess, CacheAccessRequest, JsonObject
from ..api.storage import (
    CacheEntry,
    CacheLookup,
    CacheLookupStatus,
    CacheVisibility,
    OwnerScope,
)
from ..core.context_issuer import ContextIssuer, InvalidInvocation
from ..core.ports import (
    AdmissionLease,
    AdmissionPort,
    CacheRepository,
    GrantStore,
    RevisionConflict,
    ScheduledLease,
)
from .identity import (
    InvocationPrincipalResolver,
    PrincipalResolutionDenied,
    PrincipalResolutionUnavailable,
)

_T = TypeVar("_T")


class CacheAccessError(PermissionError):
    """Controlled cache failure which never contains payload or file details."""

    def __init__(
        self,
        message: str = "cache access is unavailable",
        *,
        code: str = "cache_access_denied",
    ) -> None:
        super().__init__(message)
        self.code = code


def _bounded_key(value: object) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or any(marker in value for marker in ("/", "\\", ".."))
    ):
        raise ValueError("cache key is invalid")
    return value


class _CacheAccessCoordinator:
    __slots__ = (
        "__invocation",
        "__repository",
        "__issuer",
        "__grant_store",
        "__registration_lookup",
        "__admission",
        "__principal_resolver",
        "__clock",
    )

    def __init__(
        self,
        invocation: InvocationView,
        repository: CacheRepository,
        *,
        issuer: ContextIssuer | None = None,
        grant_store: GrantStore | None = None,
        registration_lookup: object | None = None,
        admission: AdmissionPort | None = None,
        principal_resolver: InvocationPrincipalResolver | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isinstance(invocation, InvocationView):
            raise TypeError("cache service requires an invocation")
        if not callable(getattr(repository, "get", None)) or not callable(
            getattr(repository, "put", None)
        ):
            raise TypeError("cache repository is not usable")
        if issuer is not None and not callable(getattr(issuer, "require", None)):
            raise TypeError("issuer is not usable")
        if grant_store is not None and not callable(
            getattr(grant_store, "current_grant", None)
        ):
            raise TypeError("grant store is not usable")
        if registration_lookup is not None and not callable(
            getattr(registration_lookup, "require_registered", None)
        ):
            raise TypeError("registration lookup is not usable")
        if admission is not None and not callable(getattr(admission, "check", None)):
            raise TypeError("admission port is not usable")
        if principal_resolver is not None and not callable(
            getattr(principal_resolver, "principal_id", None)
        ):
            raise TypeError("principal resolver is not usable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        object.__setattr__(self, "_CacheAccessCoordinator__invocation", invocation)
        object.__setattr__(self, "_CacheAccessCoordinator__repository", repository)
        object.__setattr__(self, "_CacheAccessCoordinator__issuer", issuer)
        object.__setattr__(self, "_CacheAccessCoordinator__grant_store", grant_store)
        object.__setattr__(
            self, "_CacheAccessCoordinator__registration_lookup", registration_lookup
        )
        object.__setattr__(self, "_CacheAccessCoordinator__admission", admission)
        object.__setattr__(
            self, "_CacheAccessCoordinator__principal_resolver", principal_resolver
        )
        object.__setattr__(self, "_CacheAccessCoordinator__clock", clock)

    def view(self) -> "CacheAccessService":
        return CacheAccessService(self)

    @property
    def invocation(self) -> InvocationView:
        return self.__invocation

    async def _admit(self) -> None:
        invocation = self.__invocation
        self._check_admission()
        if invocation.deadline is not None and invocation.deadline <= self.__clock():
            raise CacheAccessError(
                "invocation deadline has expired", code="invocation_expired"
            )
        await self._assert_grant_current()

    async def _assert_grant_current(self) -> datetime | None:
        invocation = self.__invocation
        if invocation.grant_id is None:
            return None
        if self.__grant_store is None:
            raise CacheAccessError(
                "grant authority is unavailable", code="grant_unavailable"
            )
        self._check_admission()
        principal_id = await self._principal_id()
        try:
            grant = await self.__grant_store.current_grant(invocation.grant_id)
        except Exception as exc:
            self._check_admission()
            raise CacheAccessError(
                "grant authority is unavailable", code="grant_unavailable"
            ) from exc
        except BaseException:
            self._check_admission()
            raise
        self._check_admission()
        status = getattr(getattr(grant, "status", None), "value", None)
        if status == "expired":
            raise CacheAccessError("grant has expired", code="grant_expired")
        if grant is None or status != "active":
            raise CacheAccessError("grant is no longer active", code="grant_revoked")
        if (
            grant.revision != invocation.grant_revision
            or grant.principal_id != principal_id
            or grant.module_id != invocation.module_id
        ):
            raise CacheAccessError(
                "grant does not match invocation", code="grant_mismatch"
            )
        if grant.expires_at is not None and grant.expires_at <= datetime.now(UTC):
            raise CacheAccessError("grant has expired", code="grant_expired")
        return grant.expires_at

    def _check_admission(self) -> None:
        """Require the exact issuer sidecar lease at every access boundary."""
        issuer = self.__issuer
        admission = self.__admission
        if issuer is None or admission is None:
            raise CacheAccessError(
                "admission authority is unavailable", code="admission_unavailable"
            )
        try:
            issuer.require(self.__invocation)
            lease = issuer.lease_for(self.__invocation)
            if not isinstance(lease, (AdmissionLease, ScheduledLease)):
                raise InvalidInvocation("invocation has no admission lease")
            admission.check(lease)
        except CacheAccessError:
            raise
        except Exception as exc:
            raise CacheAccessError(
                "invocation admission is no longer active", code="admission_denied"
            ) from exc

    async def _await_boundary(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        self._check_admission()
        await self._assert_grant_current()
        try:
            result = await operation()
        except asyncio.CancelledError:
            # Accepted SQLite jobs drain in their worker. Do not enqueue a
            # second authorization read while propagating caller cancellation.
            raise
        except BaseException:
            self._check_admission()
            await self._assert_grant_current()
            raise
        self._check_admission()
        await self._assert_grant_current()
        return result

    @asynccontextmanager
    async def _write_mutation(self, owner: str) -> AsyncIterator[None]:
        if self.__invocation.grant_id is None:
            yield
            return
        admission = self.__admission
        if admission is None:
            raise CacheAccessError(
                "admission authority is unavailable", code="admission_unavailable"
            )
        async with admission.mutation(owner):
            await self._assert_grant_current()
            yield

    async def _principal_id(self) -> str:
        resolver = self.__principal_resolver
        if resolver is None:
            raise CacheAccessError(
                "principal authority is unavailable", code="principal_unavailable"
            )
        self._check_admission()
        try:
            principal_id = await resolver.principal_id(self.__invocation)
        except PrincipalResolutionDenied:
            raise CacheAccessError(
                "principal is unavailable", code="principal_denied"
            ) from None
        except PrincipalResolutionUnavailable:
            raise CacheAccessError(
                "principal authority is unavailable", code="principal_unavailable"
            ) from None
        except Exception:
            raise CacheAccessError(
                "principal is unavailable", code="principal_denied"
            ) from None
        self._check_admission()
        return principal_id

    async def _scope(self, visibility: CacheVisibility) -> OwnerScope:
        invocation = self.__invocation
        if not isinstance(visibility, CacheVisibility):
            raise TypeError("cache visibility is invalid")
        if visibility is CacheVisibility.PUBLIC:
            return OwnerScope.public()
        if invocation.actor_id is None:
            raise CacheAccessError("user scope requires an actor", code="scope_denied")
        principal_id = await self._principal_id()
        if visibility is CacheVisibility.USER:
            return OwnerScope.user(principal_id)
        if invocation.grant_id is None or invocation.grant_revision is None:
            raise CacheAccessError(
                "authorised scope requires a grant", code="scope_denied"
            )
        from ..api.storage import GrantReference

        return OwnerScope.authorized(
            principal_id,
            GrantReference(invocation.grant_id, invocation.grant_revision),
        )

    async def lookup(self, request: CacheAccessRequest) -> CacheLookup:
        await self._admit()
        if not isinstance(request, CacheAccessRequest):
            raise TypeError("cache request is invalid")
        request = CacheAccessRequest(request.key, request.visibility, request.scope)
        expected = await self._scope(request.visibility)
        if request.scope != expected:
            raise CacheAccessError(
                "cache scope is outside invocation", code="scope_denied"
            )
        result = await self._await_boundary(lambda: self.__repository.get(request))
        if not isinstance(result, CacheLookup):
            raise ValueError("cache repository returned an invalid lookup")
        if result.status is CacheLookupStatus.HIT:
            if result.entry is None or result.entry.key != request.key:
                raise ValueError("cache repository returned an invalid entry")
            if result.entry.expires_at <= datetime.now(UTC):
                return CacheLookup(CacheLookupStatus.EXPIRED)
        return result

    async def get(self, key: str) -> CacheEntry | None:
        visibility = self._default_visibility()
        result = await self.lookup(
            CacheAccessRequest(
                _bounded_key(key), visibility, await self._scope(visibility)
            )
        )
        return result.entry if result.status is CacheLookupStatus.HIT else None

    async def put(
        self,
        key: str,
        payload: JsonObject,
        *,
        ttl_seconds: float,
        expected_revision: int | None = None,
        visibility: CacheVisibility | None = None,
    ) -> CacheEntry:
        await self._admit()
        key = _bounded_key(key)
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(float(ttl_seconds))
            or ttl_seconds <= 0
        ):
            raise ValueError("cache ttl must be a finite positive number")
        if visibility is None:
            visibility = self._default_visibility()
        scope = await self._scope(visibility)
        request = CacheAccessRequest(key, visibility, scope)
        entry = CacheEntry(
            key, payload, datetime.now(UTC) + timedelta(seconds=float(ttl_seconds))
        )
        if expected_revision is None:
            expected_revision = await self._await_boundary(
                lambda: self.__repository.current_revision(request)
            )
        try:
            async with self._write_mutation("cache-put"):
                grant_deadline = await self._assert_grant_current()
                authorization_deadline = (
                    grant_deadline if scope.grant is not None else None
                )
                return await self._await_boundary(
                    lambda: self.__repository.put(
                        request,
                        entry,
                        expected_revision=expected_revision,
                        authorization_deadline=authorization_deadline,
                    )
                )
        except RevisionConflict:
            raise

    async def invalidate(self, request_or_key: CacheAccessRequest | str) -> None:
        await self._admit()
        if isinstance(request_or_key, str):
            visibility = self._default_visibility()
            request = CacheAccessRequest(
                _bounded_key(request_or_key), visibility, await self._scope(visibility)
            )
        else:
            request = CacheAccessRequest(
                request_or_key.key, request_or_key.visibility, request_or_key.scope
            )
            if request.scope != await self._scope(request.visibility):
                raise CacheAccessError(
                    "cache scope is outside invocation", code="scope_denied"
                )
        invalidate = getattr(self.__repository, "invalidate", None)
        if not callable(invalidate):
            raise CacheAccessError(
                "cache invalidation is unavailable", code="cache_unavailable"
            )
        async with self._write_mutation("cache-invalidate"):
            grant_deadline = await self._assert_grant_current()
            authorization_deadline = (
                grant_deadline if request.scope.grant is not None else None
            )
            await self._await_boundary(
                lambda: invalidate(
                    request, authorization_deadline=authorization_deadline
                )
            )

    def _default_visibility(self) -> CacheVisibility:
        if self.__invocation.grant_id is not None:
            return CacheVisibility.AUTHORIZED
        if self.__invocation.actor_id is not None:
            return CacheVisibility.USER
        return CacheVisibility.PUBLIC


class CacheAccessService(CacheAccess):
    """The module-facing cache facade; storage and grant ports stay private."""

    __slots__ = ("__coordinator",)

    def __init__(
        self,
        coordinator_or_invocation: _CacheAccessCoordinator | InvocationView,
        repository: CacheRepository | None = None,
        **kwargs: object,
    ) -> None:
        if isinstance(coordinator_or_invocation, _CacheAccessCoordinator):
            if repository is not None or kwargs:
                raise TypeError("coordinator facade does not accept storage arguments")
            coordinator = coordinator_or_invocation
        else:
            if repository is None:
                raise TypeError("cache service requires a repository")
            coordinator = _CacheAccessCoordinator(
                coordinator_or_invocation, repository, **kwargs
            )
        object.__setattr__(self, "_CacheAccessService__coordinator", coordinator)

    async def get(self, key: str) -> CacheEntry | None:
        return await self.__coordinator.get(key)

    async def lookup(self, request: CacheAccessRequest) -> CacheLookup:
        return await self.__coordinator.lookup(request)

    async def put(
        self,
        key: str,
        payload: JsonObject,
        *,
        ttl_seconds: float,
        expected_revision: int | None = None,
        visibility: CacheVisibility | None = None,
    ) -> CacheEntry:
        return await self.__coordinator.put(
            key,
            payload,
            ttl_seconds=ttl_seconds,
            expected_revision=expected_revision,
            visibility=visibility,
        )

    async def invalidate(self, request_or_key: CacheAccessRequest | str) -> None:
        await self.__coordinator.invalidate(request_or_key)

    def __getattr__(self, name: str) -> object:
        if name in {"repository", "grant_store", "invocation", "scope"}:
            raise AttributeError(name)
        raise AttributeError(name)


CacheAccessCoordinator = _CacheAccessCoordinator
CacheService = CacheAccessService

__all__ = [
    "CacheAccessCoordinator",
    "CacheAccessError",
    "CacheAccessService",
    "CacheService",
]
