"""Invocation-bound resource registration and reads."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Callable, TypeVar
from uuid import uuid4

from ..api.contexts import InvocationView
from ..api.services import ResourceAccess, ResourceReference
from ..api.storage import (
    CacheVisibility,
    OwnerScope,
    ResourceMetadata,
)
from ..core.context_issuer import ContextIssuer, InvalidInvocation
from ..core.ports import (
    AdmissionLease,
    AdmissionPort,
    FileStage,
    GrantStore,
    ResourceRepository,
    SafeFileStore,
    ScheduledLease,
)
from .identity import (
    InvocationPrincipalResolver,
    PrincipalResolutionDenied,
    PrincipalResolutionUnavailable,
)

_T = TypeVar("_T")


class ResourceAccessError(PermissionError):
    """Controlled resource failure without paths, bytes, or secret material."""

    def __init__(
        self,
        message: str = "resource access is unavailable",
        *,
        code: str = "resource_access_denied",
    ) -> None:
        super().__init__(message)
        self.code = code


def _asset_id(value: object) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or any(char in value for char in ("/", "\\", "\n", "\r", ".."))
    ):
        raise ValueError("asset id is invalid")
    return value


class _ResourceAccessCoordinator:
    __slots__ = (
        "__invocation",
        "__repository",
        "__file_store",
        "__issuer",
        "__grant_store",
        "__registration_lookup",
        "__admission",
        "__principal_resolver",
        "__clock",
        "__resource_ttl",
    )

    def __init__(
        self,
        invocation: InvocationView,
        repository: ResourceRepository,
        file_store: SafeFileStore | None = None,
        *,
        issuer: ContextIssuer | None = None,
        grant_store: GrantStore | None = None,
        registration_lookup: object | None = None,
        admission: AdmissionPort | None = None,
        principal_resolver: InvocationPrincipalResolver | None = None,
        clock: Callable[[], float] = monotonic,
        resource_ttl_seconds: float | None = None,
    ) -> None:
        if not isinstance(invocation, InvocationView):
            raise TypeError("resource service requires an invocation")
        if not all(
            callable(getattr(repository, method, None))
            for method in ("metadata", "register", "read")
        ):
            raise TypeError("resource repository is not usable")
        if file_store is None:
            file_store = getattr(repository, "file_store", None)
        if not all(
            callable(getattr(file_store, method, None))
            for method in ("stage", "commit", "read", "discard", "mark_orphan")
        ):
            raise TypeError("safe file store is not usable")
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
        if resource_ttl_seconds is not None and (
            isinstance(resource_ttl_seconds, bool)
            or not isinstance(resource_ttl_seconds, (int, float))
            or not math.isfinite(float(resource_ttl_seconds))
            or resource_ttl_seconds <= 0
        ):
            raise ValueError("resource ttl must be a finite positive number")
        object.__setattr__(self, "_ResourceAccessCoordinator__invocation", invocation)
        object.__setattr__(self, "_ResourceAccessCoordinator__repository", repository)
        object.__setattr__(self, "_ResourceAccessCoordinator__file_store", file_store)
        object.__setattr__(self, "_ResourceAccessCoordinator__issuer", issuer)
        object.__setattr__(self, "_ResourceAccessCoordinator__grant_store", grant_store)
        object.__setattr__(
            self, "_ResourceAccessCoordinator__registration_lookup", registration_lookup
        )
        object.__setattr__(self, "_ResourceAccessCoordinator__admission", admission)
        object.__setattr__(
            self, "_ResourceAccessCoordinator__principal_resolver", principal_resolver
        )
        object.__setattr__(self, "_ResourceAccessCoordinator__clock", clock)
        object.__setattr__(
            self, "_ResourceAccessCoordinator__resource_ttl", resource_ttl_seconds
        )

    def view(self) -> "ResourceAccessService":
        return ResourceAccessService(self)

    async def _admit(self) -> None:
        invocation = self.__invocation
        self._check_admission()
        if invocation.deadline is not None and invocation.deadline <= self.__clock():
            raise ResourceAccessError(
                "invocation deadline has expired", code="invocation_expired"
            )
        await self._assert_grant_current()

    async def _assert_grant_current(self) -> datetime | None:
        invocation = self.__invocation
        if invocation.grant_id is None:
            return None
        if self.__grant_store is None:
            raise ResourceAccessError(
                "grant authority is unavailable", code="grant_unavailable"
            )
        self._check_admission()
        principal_id = await self._principal_id()
        try:
            grant = await self.__grant_store.current_grant(invocation.grant_id)
        except Exception as exc:
            self._check_admission()
            raise ResourceAccessError(
                "grant authority is unavailable", code="grant_unavailable"
            ) from exc
        except BaseException:
            self._check_admission()
            raise
        self._check_admission()
        status = getattr(getattr(grant, "status", None), "value", None)
        if status == "expired":
            raise ResourceAccessError("grant has expired", code="grant_expired")
        if grant is None or status != "active":
            raise ResourceAccessError("grant is no longer active", code="grant_revoked")
        if (
            grant.revision != invocation.grant_revision
            or grant.principal_id != principal_id
            or grant.module_id != invocation.module_id
        ):
            raise ResourceAccessError(
                "grant does not match invocation", code="grant_mismatch"
            )
        if grant.expires_at is not None and grant.expires_at <= datetime.now(UTC):
            raise ResourceAccessError("grant has expired", code="grant_expired")
        return grant.expires_at

    def _check_admission(self) -> None:
        """Require the exact issuer sidecar lease at every access boundary."""
        issuer = self.__issuer
        admission = self.__admission
        if issuer is None or admission is None:
            raise ResourceAccessError(
                "admission authority is unavailable", code="admission_unavailable"
            )
        try:
            issuer.require(self.__invocation)
            lease = issuer.lease_for(self.__invocation)
            if not isinstance(lease, (AdmissionLease, ScheduledLease)):
                raise InvalidInvocation("invocation has no admission lease")
            admission.check(lease)
        except ResourceAccessError:
            raise
        except Exception as exc:
            raise ResourceAccessError(
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
            raise ResourceAccessError(
                "admission authority is unavailable", code="admission_unavailable"
            )
        async with admission.mutation(owner):
            await self._assert_grant_current()
            yield

    async def _recover_registration_failure(
        self,
        stage: FileStage,
        metadata: ResourceMetadata,
        failure: BaseException,
    ) -> None:
        """Orphan only after an immediately available read proves index absence.

        Cancellation, a closed/full SQLite executor, an unavailable status port,
        and any mismatched row all leave the pending marker as the recovery owner.
        """
        task = asyncio.current_task()
        if isinstance(failure, asyncio.CancelledError) or (
            task is not None and task.cancelling()
        ):
            return
        registration_state = getattr(self.__repository, "registration_state", None)
        if not callable(registration_state):
            return
        database = getattr(self.__repository, "database", None)
        executor = getattr(database, "executor", None)
        if executor is not None:
            if getattr(executor, "state", None) != "OPEN":
                return
            state_lock = getattr(executor, "_state_lock", None)
            if state_lock is not None:
                with state_lock:
                    if (
                        getattr(executor, "_outstanding", 0) != 0
                        or not getattr(
                            getattr(executor, "_queue", None), "empty", lambda: True
                        )()
                    ):
                        return
        try:
            state = await registration_state(metadata.asset_id, metadata.scope)
        except BaseException:
            return
        if state is not None:
            # Compare the complete resource snapshot. A matching revision proves
            # registration survived confirm or a postcheck expiry; a mismatched
            # row is uncertain and must retain the pending recovery marker.
            registered_exactly = state == metadata
            if registered_exactly:
                return
            return
        try:
            await self.__file_store.mark_orphan(stage, "resource index absent")
        except BaseException:
            # Preserve the pending marker if compensation cannot be confirmed.
            return

    async def _discard_uncommitted_stage(self, stage: FileStage) -> None:
        """Remove this call's temporary bytes, or make them discoverable."""
        try:
            await self.__file_store.discard(stage)
            return
        except BaseException:
            pass
        try:
            await self.__file_store.mark_orphan(stage, "resource stage cleanup failed")
        except BaseException:
            # Both LocalSafeFileStore operations are synchronous filesystem
            # calls behind async methods. If a custom store is unavailable,
            # keep the original failure and avoid a detached cleanup task.
            return

    async def _principal_id(self) -> str:
        resolver = self.__principal_resolver
        if resolver is None:
            raise ResourceAccessError(
                "principal authority is unavailable", code="principal_unavailable"
            )
        self._check_admission()
        try:
            principal_id = await resolver.principal_id(self.__invocation)
        except PrincipalResolutionDenied:
            raise ResourceAccessError(
                "principal is unavailable", code="principal_denied"
            ) from None
        except PrincipalResolutionUnavailable:
            raise ResourceAccessError(
                "principal authority is unavailable", code="principal_unavailable"
            ) from None
        except Exception:
            raise ResourceAccessError(
                "principal is unavailable", code="principal_denied"
            ) from None
        self._check_admission()
        return principal_id

    async def _scope(self, visibility: CacheVisibility | None = None) -> OwnerScope:
        invocation = self.__invocation
        if visibility is None:
            visibility = self._default_visibility()
        if visibility is CacheVisibility.PUBLIC:
            return OwnerScope.public()
        if invocation.actor_id is None:
            raise ResourceAccessError(
                "user scope requires an actor", code="scope_denied"
            )
        principal_id = await self._principal_id()
        if visibility is CacheVisibility.USER:
            return OwnerScope.user(principal_id)
        if invocation.grant_id is None or invocation.grant_revision is None:
            raise ResourceAccessError(
                "authorised scope requires a grant", code="scope_denied"
            )
        from ..api.storage import GrantReference

        return OwnerScope.authorized(
            principal_id,
            GrantReference(invocation.grant_id, invocation.grant_revision),
        )

    async def register(
        self,
        asset_id: str,
        media_type: str,
        content: bytes,
        *,
        visibility: CacheVisibility | None = None,
        ttl_seconds: float | None = None,
        expires_at: datetime | None = None,
    ) -> ResourceReference:
        await self._admit()
        asset_id = _asset_id(asset_id)
        if not isinstance(content, bytes):
            raise TypeError("resource content must be bytes")
        if (
            not isinstance(media_type, str)
            or not media_type.strip()
            or "\n" in media_type
            or "\r" in media_type
        ):
            raise ValueError("media type is invalid")
        if visibility is None:
            visibility = self._default_visibility()
        if not isinstance(visibility, CacheVisibility):
            raise TypeError("resource visibility is invalid")
        scope = await self._scope(visibility)
        if ttl_seconds is not None:
            if (
                isinstance(ttl_seconds, bool)
                or not isinstance(ttl_seconds, (int, float))
                or not math.isfinite(float(ttl_seconds))
                or ttl_seconds <= 0
            ):
                raise ValueError("resource ttl must be a finite positive number")
            expires_at = datetime.now(UTC) + timedelta(seconds=float(ttl_seconds))
        elif expires_at is None and self.__resource_ttl is not None:
            expires_at = datetime.now(UTC) + timedelta(
                seconds=float(self.__resource_ttl)
            )
        if expires_at is not None and (
            expires_at.tzinfo is None or expires_at.utcoffset() is None
        ):
            raise ValueError("resource expiry must be timezone-aware")
        operation_id = f"resource_{uuid4().hex}"
        self._check_admission()
        await self._assert_grant_current()
        try:
            stage = await self.__file_store.stage(operation_id, content, scope)
        except asyncio.CancelledError:
            raise
        except BaseException:
            self._check_admission()
            await self._assert_grant_current()
            raise
        committed = False
        try:
            self._check_admission()
            await self._assert_grant_current()
            if stage.asset_id != asset_id:
                raise ValueError("asset id does not match content")
            metadata = ResourceMetadata(
                asset_id, media_type, scope, len(content), expires_at
            )
            async with self._write_mutation("resource-register"):
                try:
                    self._check_admission()
                    await self._assert_grant_current()
                    await self.__file_store.commit(stage, metadata)
                    committed = True
                    self._check_admission()
                    grant_deadline = await self._assert_grant_current()
                    authorization_deadline = (
                        grant_deadline if scope.grant is not None else None
                    )
                    registered = await self._await_boundary(
                        lambda: self.__repository.register(
                            metadata,
                            authorization_deadline=authorization_deadline,
                        )
                    )
                except BaseException as failure:
                    if committed:
                        await self._recover_registration_failure(
                            stage, metadata, failure
                        )
                    raise
        except BaseException:
            if not committed:
                await self._discard_uncommitted_stage(stage)
            raise
        return ResourceReference(
            registered.asset_id, registered.media_type, registered.scope
        )

    async def read(
        self, asset_id: str, *, visibility: CacheVisibility | None = None
    ) -> bytes:
        await self._admit()
        asset_id = _asset_id(asset_id)
        scopes = await self._read_scopes(visibility)
        for scope in scopes:
            metadata = await self._await_boundary(
                lambda: self.__repository.metadata(asset_id, scope)
            )
            if metadata is None or metadata.scope != scope:
                continue
            content = await self._await_boundary(
                lambda: self.__repository.read(asset_id, scope)
            )
            if not isinstance(content, bytes):
                raise ValueError("resource repository returned invalid content")
            return content
        raise FileNotFoundError("resource is unavailable")

    async def metadata(
        self, asset_id: str, *, visibility: CacheVisibility | None = None
    ) -> ResourceMetadata | None:
        await self._admit()
        asset_id = _asset_id(asset_id)
        for scope in await self._read_scopes(visibility):
            metadata = await self._await_boundary(
                lambda: self.__repository.metadata(asset_id, scope)
            )
            if metadata is not None and metadata.scope == scope:
                return metadata
        return None

    async def delete(
        self, asset_id: str, *, visibility: CacheVisibility | None = None
    ) -> None:
        await self._admit()
        delete = getattr(self.__repository, "delete", None)
        if not callable(delete):
            raise ResourceAccessError(
                "resource deletion is unavailable", code="resource_unavailable"
            )
        scopes = await self._read_scopes(visibility)
        if visibility is None and len(scopes) != 1:
            raise ResourceAccessError(
                "resource deletion requires an explicit scope", code="scope_required"
            )
        async with self._write_mutation("resource-delete"):
            grant_deadline = await self._assert_grant_current()
            authorization_deadline = (
                grant_deadline if scopes[0].grant is not None else None
            )
            await self._await_boundary(
                lambda: delete(
                    _asset_id(asset_id),
                    scopes[0],
                    authorization_deadline=authorization_deadline,
                )
            )

    async def _read_scopes(
        self, visibility: CacheVisibility | None = None
    ) -> tuple[OwnerScope, ...]:
        """Resolve trusted metadata scopes without treating asset_id as authority.

        Public metadata is checked first for actor-bearing command invocations,
        because public resources are intentionally shareable.  A private
        fallback is still bound to the current actor or grant.  Callers may
        select one visibility explicitly; the enum is only a query selector,
        while metadata and (for authorized scopes) the current Grant remain
        the authorization facts.
        """
        if visibility is not None:
            return (await self._scope(visibility),)
        default = await self._scope()
        if default.kind.value == "public":
            return (default,)
        public = OwnerScope.public()
        return (public, default)

    def _default_visibility(self) -> CacheVisibility:
        if self.__invocation.grant_id is not None:
            return CacheVisibility.AUTHORIZED
        if self.__invocation.actor_id is not None:
            return CacheVisibility.USER
        return CacheVisibility.PUBLIC


class ResourceAccessService(ResourceAccess):
    """The module-facing resource facade; no filesystem path is exposed."""

    __slots__ = ("__coordinator",)

    def __init__(
        self,
        coordinator_or_invocation: _ResourceAccessCoordinator | InvocationView,
        repository: ResourceRepository | None = None,
        file_store: SafeFileStore | None = None,
        **kwargs: object,
    ) -> None:
        if isinstance(coordinator_or_invocation, _ResourceAccessCoordinator):
            if repository is not None or file_store is not None or kwargs:
                raise TypeError("coordinator facade does not accept storage arguments")
            coordinator = coordinator_or_invocation
        else:
            if repository is None:
                raise TypeError("resource service requires a repository")
            coordinator = _ResourceAccessCoordinator(
                coordinator_or_invocation, repository, file_store, **kwargs
            )
        object.__setattr__(self, "_ResourceAccessService__coordinator", coordinator)

    async def register(
        self,
        asset_id: str,
        media_type: str,
        content: bytes,
        *,
        visibility: CacheVisibility | None = None,
        ttl_seconds: float | None = None,
        expires_at: datetime | None = None,
    ) -> ResourceReference:
        return await self.__coordinator.register(
            asset_id,
            media_type,
            content,
            visibility=visibility,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
        )

    async def read(
        self, asset_id: str, *, visibility: CacheVisibility | None = None
    ) -> bytes:
        return await self.__coordinator.read(asset_id, visibility=visibility)

    async def metadata(
        self, asset_id: str, *, visibility: CacheVisibility | None = None
    ) -> ResourceMetadata | None:
        return await self.__coordinator.metadata(asset_id, visibility=visibility)

    async def delete(
        self, asset_id: str, *, visibility: CacheVisibility | None = None
    ) -> None:
        return await self.__coordinator.delete(asset_id, visibility=visibility)


ResourceAccessCoordinator = _ResourceAccessCoordinator
ResourceService = ResourceAccessService

__all__ = [
    "ResourceAccessCoordinator",
    "ResourceAccessError",
    "ResourceAccessService",
    "ResourceService",
]
