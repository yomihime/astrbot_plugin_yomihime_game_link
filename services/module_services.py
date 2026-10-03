"""Host-side B03 assembly for module and invocation scoped services.

The objects in this module are trusted composition code.  Module-facing
handles retain only their declared operations and re-check the exact issued
invocation around asynchronous service calls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from types import MappingProxyType
from typing import Any

from ..api.contexts import InvocationOrigin, InvocationView
from ..api.manifests import (
    ConfigField,
    ModuleManifest,
    PrivacyFloor,
)
from ..api.results import CapabilityResult, ErrorCode, ErrorDetail, ResultStatus
from ..api.services import (
    AccountOperations,
    BindingView,
    CallerCapability,
    ConfigSnapshot,
    ConfigTarget,
    GrantReference,
    HttpRequest,
    HttpResponse,
    InvocationServices,
    ModuleServices,
    ResolvedIdentity,
    SubscriptionOperations,
    SubscriptionUnavailable,
    SubscriptionView,
)
from ..api.storage import OwnerScope, OwnershipKind
from ..api.subscriptions import SubscriptionRequest
from ..core.context_issuer import ContextIssuer
from ..core.lifecycle import LifecycleController
from ..core.policy import tool_allowed
from ..core.ports import (
    AdmissionLease,
    CallerCapabilityIssuer,
    ModuleNotRegistered,
    ModuleRegistrationLookup,
    ModuleRegistrationSnapshot,
    ScheduledLease,
)
from ..core.registry import RegisteredModule, Registry
from ..core.task_scope import TaskScope
from ..infrastructure.http import SourceHttpError, SourceHttpService
from ..infrastructure.sqlite.repositories import SQLiteRepositories
from .authorization import AuthorizationService, SecretAvailability
from .bindings import AccountOperationsService
from .cache import CacheAccessService
from .configuration import ConfigurationService
from .dependency_calls import DependencyInvoker
from .identity import IdentityResolverService, InvocationPrincipalResolver
from .records import ModuleRecordsService
from .resources import ResourceAccessService
from .source_credentials import (
    SourceCredentialPolicy,
    SourceCredentialService,
    bind_source_credential_declarations,
)


class InvocationBindingError(PermissionError):
    """Stable failure to bind services to a current trusted invocation."""

    code = "invocation_binding_unavailable"

    def __init__(self) -> None:
        super().__init__("invocation services are unavailable")


class RegistryRegistrationLookup(ModuleRegistrationLookup):
    """Adapt the authoritative Registry snapshot to the reviewed lookup port."""

    __slots__ = ("_registry",)

    def __init__(self, registry: Registry) -> None:
        if not isinstance(registry, Registry):
            raise TypeError("registration lookup requires a Registry")
        self._registry = registry

    async def require_registered(self, module_id: str) -> ModuleRegistrationSnapshot:
        try:
            snapshot = self._registry.snapshot()
            module = snapshot.module(module_id)
        except Exception:
            raise ModuleNotRegistered(module_id) from None
        return ModuleRegistrationSnapshot(
            module_id,
            module.enabled,
            snapshot.revision,
            module.epoch,
            module.manifest.collections,
        )


class _IssuerCallerCapabilityIssuer(CallerCapabilityIssuer):
    """Issue exact caller descriptors only from current issuer-backed views."""

    __slots__ = ("_issuer", "_registry", "_issued", "_unsubscribe")

    def __init__(self, issuer: ContextIssuer, registry: Registry) -> None:
        self._issuer = issuer
        self._registry = registry
        self._issued: dict[tuple[str, str], CallerCapability] = {}
        self._unsubscribe = issuer.add_release_observer(self._on_release)

    def _on_release(self, view: InvocationView) -> None:
        for key in tuple(self._issued):
            if key[0] == view.invocation_id:
                self._issued.pop(key, None)

    def issue(self, invocation: InvocationView, capability_id: str) -> CallerCapability:
        try:
            view = self._issuer.require(invocation)
            snapshot = self._registry.snapshot()
            module = snapshot.module(view.module_id)
            if (
                not module.enabled
                or view.module_epoch != module.epoch
                or view.capability_id != capability_id
                or not any(
                    item.capability_id == capability_id
                    for item in module.manifest.capabilities
                )
            ):
                raise ValueError
            caller = CallerCapability(
                view.module_id, capability_id, view.registry_revision, module.epoch
            )
        except Exception:
            raise InvocationBindingError() from None
        self._issued[(view.invocation_id, capability_id)] = caller
        return caller

    def require(
        self, invocation: InvocationView, caller: CallerCapability
    ) -> CallerCapability:
        try:
            view = self._issuer.require(invocation)
            snapshot = self._registry.snapshot()
            module = snapshot.module(view.module_id)
            exact = self._issued.get((view.invocation_id, view.capability_id or ""))
            if (
                exact is not caller
                or not module.enabled
                or view.capability_id != caller.capability_id
                or caller.module_id != view.module_id
                or caller.registry_revision != view.registry_revision
                or caller.module_epoch != module.epoch
                or view.module_epoch != module.epoch
            ):
                raise ValueError
        except Exception:
            raise InvocationBindingError() from None
        return caller


class UnavailableSubscriptionOperations(SubscriptionOperations):
    """Explicit B03 placeholder that checks trusted command authority first."""

    __slots__ = ("_issuer", "_module_id")

    def __init__(self, issuer: ContextIssuer, module_id: str) -> None:
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("subscriptions require a ContextIssuer")
        self._issuer = issuer
        self._module_id = module_id

    def _command(self, invocation: InvocationView) -> None:
        view = self._issuer.require(invocation)
        if (
            view.module_id != self._module_id
            or view.origin is not InvocationOrigin.COMMAND
            or view.parent_id is not None
        ):
            raise PermissionError("subscription operation is not permitted")

    async def create(
        self, invocation: InvocationView, subscription: SubscriptionView
    ) -> SubscriptionView:
        self._command(invocation)
        raise SubscriptionUnavailable()

    async def revise(
        self, invocation: InvocationView, subscription: SubscriptionView
    ) -> SubscriptionView:
        self._command(invocation)
        raise SubscriptionUnavailable()

    async def create_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        self._command(invocation)
        raise SubscriptionUnavailable()

    async def revise_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        self._command(invocation)
        raise SubscriptionUnavailable()

    async def list_current(
        self, invocation: InvocationView
    ) -> tuple[SubscriptionView, ...]:
        self._command(invocation)
        raise SubscriptionUnavailable()

    async def cancel(
        self,
        invocation: InvocationView,
        subscription_id: str,
        *,
        expected_revision: int,
    ) -> None:
        self._command(invocation)
        raise SubscriptionUnavailable()


def _require_command_only_entry(
    issuer: ContextIssuer,
    registry: Registry,
    lifecycle: LifecycleController,
    module_id: str,
    invocation: InvocationView,
) -> RegisteredModule:
    try:
        view = issuer.require(invocation)
        if (
            view.module_id != module_id
            or view.origin is not InvocationOrigin.COMMAND
            or view.parent_id is not None
            or view.capability_id is None
        ):
            raise ValueError
        module = registry.snapshot().module(module_id)
        if not module.enabled or module.epoch != view.module_epoch:
            raise ValueError
        if not any(
            item.capability_id == view.capability_id
            for item in module.manifest.capabilities
        ):
            raise ValueError
        lease = issuer.lease_for(view)
        if not isinstance(lease, AdmissionLease):
            raise ValueError
        lifecycle.guard(module_id, epoch=view.module_epoch, invocation=view)
        lifecycle.admission.check(lease)
        return module
    except Exception:
        raise InvocationBindingError() from None


class _BoundSubscriptionOperations(SubscriptionOperations):
    """Bind the shared SDK Protocol service to one module and root command."""

    __slots__ = (
        "_issuer",
        "_registry",
        "_lifecycle",
        "_module_id",
        "_delegate",
        "_owner_authority",
    )

    def __init__(
        self,
        issuer: ContextIssuer,
        registry: Registry,
        lifecycle: LifecycleController,
        module_id: str,
        delegate: SubscriptionOperations,
        owner_authority: object | None,
    ) -> None:
        self._issuer = issuer
        self._registry = registry
        self._lifecycle = lifecycle
        self._module_id = module_id
        self._delegate = delegate
        self._owner_authority = owner_authority

    def _command(self, invocation: InvocationView) -> RegisteredModule:
        return _require_command_only_entry(
            self._issuer,
            self._registry,
            self._lifecycle,
            self._module_id,
            invocation,
        )

    async def _owner_check(self, invocation: InvocationView) -> None:
        module = self._command(invocation)
        capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == invocation.capability_id
            ),
            None,
        )
        if capability is None or capability.privacy_floor is not PrivacyFloor.OWNER:
            return
        if (
            invocation.grant_id is not None
            or invocation.grant_revision is not None
            or self._owner_authority is None
        ):
            raise InvocationBindingError()
        try:
            await self._owner_authority.require_current(invocation)
        except Exception:
            raise InvocationBindingError() from None
        self._command(invocation)

    async def create(
        self, invocation: InvocationView, subscription: SubscriptionView
    ) -> SubscriptionView:
        await self._owner_check(invocation)
        result = await self._delegate.create(invocation, subscription)
        await self._owner_check(invocation)
        return result

    async def revise(
        self, invocation: InvocationView, subscription: SubscriptionView
    ) -> SubscriptionView:
        await self._owner_check(invocation)
        result = await self._delegate.revise(invocation, subscription)
        await self._owner_check(invocation)
        return result

    async def create_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        await self._owner_check(invocation)
        result = await self._delegate.create_request(invocation, request)
        await self._owner_check(invocation)
        return result

    async def revise_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        await self._owner_check(invocation)
        result = await self._delegate.revise_request(invocation, request)
        await self._owner_check(invocation)
        return result

    async def list_current(
        self, invocation: InvocationView
    ) -> tuple[SubscriptionView, ...]:
        await self._owner_check(invocation)
        result = await self._delegate.list_current(invocation)
        await self._owner_check(invocation)
        return result

    async def cancel(
        self,
        invocation: InvocationView,
        subscription_id: str,
        *,
        expected_revision: int,
    ) -> None:
        await self._owner_check(invocation)
        await self._delegate.cancel(
            invocation, subscription_id, expected_revision=expected_revision
        )
        await self._owner_check(invocation)


class _AccountOperations(AccountOperations):
    """Join the reviewed A1 binding facade and A2 authorization service."""

    __slots__ = ("_bindings", "_authorization")

    def __init__(
        self, bindings: AccountOperationsService, authorization: AuthorizationService
    ) -> None:
        self._bindings = bindings
        self._authorization = authorization

    async def status(self, invocation: InvocationView) -> GrantReference | None:
        return await self._authorization.status(invocation)

    async def begin_login(self, invocation: InvocationView) -> str:
        return await self._authorization.begin_login(invocation)

    async def bind(
        self, invocation: InvocationView, identity: ResolvedIdentity
    ) -> BindingView:
        return await self._bindings.bind(invocation, identity)

    async def bindings(self, invocation: InvocationView) -> tuple[BindingView, ...]:
        return await self._bindings.bindings(invocation)

    async def unbind(
        self, invocation: InvocationView, binding_id: str, *, expected_revision: int
    ) -> None:
        await self._bindings.unbind(
            invocation, binding_id, expected_revision=expected_revision
        )

    async def revoke(self, invocation: InvocationView, grant: GrantReference) -> None:
        await self._authorization.revoke(invocation, grant)


class _BoundIdentityResolver:
    __slots__ = ("_issuer", "_registry", "_lifecycle", "_module_id", "_delegate")

    def __init__(self, issuer, registry, lifecycle, module_id, delegate) -> None:
        self._issuer = issuer
        self._registry = registry
        self._lifecycle = lifecycle
        self._module_id = module_id
        self._delegate = delegate

    async def default_identity(self, invocation: InvocationView):
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        result = await self._delegate.default_identity(invocation)
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        return result


class _BoundAccountOperations(AccountOperations):
    __slots__ = ("_issuer", "_registry", "_lifecycle", "_module_id", "_delegate")

    def __init__(self, issuer, registry, lifecycle, module_id, delegate) -> None:
        self._issuer = issuer
        self._registry = registry
        self._lifecycle = lifecycle
        self._module_id = module_id
        self._delegate = delegate

    async def _call(self, name: str, invocation: InvocationView, *args, **kwargs):
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        result = await getattr(self._delegate, name)(invocation, *args, **kwargs)
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        return result

    async def status(self, invocation: InvocationView):
        return await self._call("status", invocation)

    async def begin_login(self, invocation: InvocationView):
        return await self._call("begin_login", invocation)

    async def bind(self, invocation: InvocationView, identity: ResolvedIdentity):
        return await self._call("bind", invocation, identity)

    async def bindings(self, invocation: InvocationView):
        return await self._call("bindings", invocation)

    async def unbind(
        self, invocation: InvocationView, binding_id: str, *, expected_revision: int
    ) -> None:
        await self._call(
            "unbind", invocation, binding_id, expected_revision=expected_revision
        )

    async def revoke(self, invocation: InvocationView, grant: GrantReference) -> None:
        await self._call("revoke", invocation, grant)


class _BoundRecordCollection:
    __slots__ = ("__collection", "__check_sync", "__check")
    _allowed = frozenset({"scope", "get", "create", "replace", "query", "delete"})

    def __init__(
        self,
        collection: object,
        check_sync: Callable[[], None],
        check: Callable[[], Awaitable[None]],
    ) -> None:
        object.__setattr__(self, "_BoundRecordCollection__collection", collection)
        object.__setattr__(self, "_BoundRecordCollection__check_sync", check_sync)
        object.__setattr__(self, "_BoundRecordCollection__check", check)

    def __getattr__(self, name: str) -> object:
        if name not in self._allowed:
            raise AttributeError(name)
        target = getattr(self.__collection, name)
        if name == "scope":
            self.__check_sync()
            return target
        if not callable(target):
            raise AttributeError(name)

        async def invoke(*args: object, **kwargs: object) -> object:
            await self.__check()
            result = await target(*args, **kwargs)
            await self.__check()
            return result

        return invoke


class _BoundRecords:
    __slots__ = ("__collection", "__check_sync", "__check")

    def __init__(
        self,
        collection: Callable[[str], Awaitable[object]],
        check_sync: Callable[[], None],
        check: Callable[[], Awaitable[None]],
    ) -> None:
        object.__setattr__(self, "_BoundRecords__collection", collection)
        object.__setattr__(self, "_BoundRecords__check_sync", check_sync)
        object.__setattr__(self, "_BoundRecords__check", check)

    async def collection(self, name: str) -> _BoundRecordCollection:
        await self.__check()
        collection = await self.__collection(name)
        await self.__check()
        return _BoundRecordCollection(collection, self.__check_sync, self.__check)


class _BoundCache:
    __slots__ = ("__cache", "__check_sync", "__check")

    def __init__(
        self,
        cache: CacheAccessService,
        check_sync: Callable[[], None],
        check: Callable[[], Awaitable[None]],
    ) -> None:
        object.__setattr__(self, "_BoundCache__cache", cache)
        object.__setattr__(self, "_BoundCache__check_sync", check_sync)
        object.__setattr__(self, "_BoundCache__check", check)

    async def get(self, key: str):
        await self.__check()
        result = await self.__cache.get(key)
        await self.__check()
        return result

    async def lookup(self, request: object):
        await self.__check()
        result = await self.__cache.lookup(request)
        await self.__check()
        return result

    async def put(self, key: str, payload: Mapping[str, object], *, ttl_seconds: float):
        await self.__check()
        result = await self.__cache.put(key, payload, ttl_seconds=ttl_seconds)
        await self.__check()
        return result


class _BoundResources:
    __slots__ = ("__resources", "__check_sync", "__check")

    def __init__(
        self,
        resources: ResourceAccessService,
        check_sync: Callable[[], None],
        check: Callable[[], Awaitable[None]],
    ) -> None:
        object.__setattr__(self, "_BoundResources__resources", resources)
        object.__setattr__(self, "_BoundResources__check_sync", check_sync)
        object.__setattr__(self, "_BoundResources__check", check)

    async def register(self, asset_id: str, media_type: str, content: bytes):
        await self.__check()
        result = await self.__resources.register(asset_id, media_type, content)
        await self.__check()
        return result

    async def read(self, asset_id: str) -> bytes:
        await self.__check()
        result = await self.__resources.read(asset_id)
        await self.__check()
        return result


class _BoundHttp:
    __slots__ = ("__http", "__check_sync", "__check")

    def __init__(
        self,
        http: SourceHttpService,
        check_sync: Callable[[], None],
        check: Callable[[], Awaitable[None]],
    ) -> None:
        object.__setattr__(self, "_BoundHttp__http", http)
        object.__setattr__(self, "_BoundHttp__check_sync", check_sync)
        object.__setattr__(self, "_BoundHttp__check", check)

    async def fetch(self, request: HttpRequest) -> HttpResponse:
        try:
            await self.__check()
        except InvocationBindingError:
            raise SourceHttpError("request_rejected") from None
        response = await self.__http.fetch(request)
        try:
            await self.__check()
        except InvocationBindingError:
            raise SourceHttpError("request_rejected") from None
        return response


class _BoundTasks:
    __slots__ = ("__scope", "__check_sync", "__check")

    def __init__(
        self,
        scope: TaskScope,
        check_sync: Callable[[], None],
        check: Callable[[], Awaitable[None]],
    ) -> None:
        object.__setattr__(self, "_BoundTasks__scope", scope)
        object.__setattr__(self, "_BoundTasks__check_sync", check_sync)
        object.__setattr__(self, "_BoundTasks__check", check)

    def create_task(self, work: Awaitable[object], *, name: str):
        try:
            self.__check_sync()
        except BaseException:
            close = getattr(work, "close", None)
            if callable(close):
                close()
            raise

        started = False

        def close_unstarted(_task=None) -> None:
            if not started:
                close = getattr(work, "close", None)
                if callable(close):
                    close()

        async def guarded() -> object:
            nonlocal started
            try:
                await self.__check()
            except BaseException:
                close = getattr(work, "close", None)
                if callable(close):
                    close()
                raise
            started = True
            result = await work
            await self.__check()
            return result

        try:
            task = self.__scope.create_task(guarded(), name=name)
        except BaseException:
            close_unstarted()
            raise
        task.add_done_callback(close_unstarted)
        return task

    async def await_result(self, work: Awaitable[Any]) -> Any:
        try:
            await self.__check()
        except BaseException:
            close = getattr(work, "close", None)
            if callable(close):
                close()
            raise
        result = await self.__scope.await_result(work)
        await self.__check()
        return result

    @property
    def deadline_monotonic(self) -> float | None:
        self.__check_sync()
        return self.__scope.deadline_monotonic


class _BoundDependencies:
    __slots__ = ("__dependencies", "__check")

    def __init__(
        self,
        dependencies: DependencyInvoker | None,
        check: Callable[[], Awaitable[None]],
    ) -> None:
        object.__setattr__(self, "_BoundDependencies__dependencies", dependencies)
        object.__setattr__(self, "_BoundDependencies__check", check)

    @property
    def caller_capability(self) -> CallerCapability:
        if self.__dependencies is None:
            raise InvocationBindingError()
        return self.__dependencies.caller_capability

    async def invoke(
        self, invocation: InvocationView, capability: object, parameters: object
    ):
        await self.__check()
        if self.__dependencies is None:
            return CapabilityResult(
                "dependency-unavailable",
                ResultStatus.ERROR,
                error=ErrorDetail(
                    ErrorCode.UNSUPPORTED,
                    "dependency invocation is unavailable for this context",
                ),
            )
        result = await self.__dependencies.invoke(invocation, capability, parameters)
        await self.__check()
        return result


@dataclass(frozen=True, slots=True)
class _InvocationServices(InvocationServices):
    invocation: InvocationView
    records: _BoundRecords
    cache: _BoundCache
    http: _BoundHttp
    resources: _BoundResources
    dependencies: _BoundDependencies
    tasks: _BoundTasks


class InvocationServiceBinder:
    """Bind reviewed services to one exact issuer-created invocation."""

    __slots__ = (
        "_module_id",
        "_registry",
        "_issuer",
        "_lifecycle",
        "_repositories",
        "_authorization",
        "_principal_resolver",
        "_owner_authority",
        "_caller_issuer",
        "_http",
        "_clock",
        "_utc_clock",
    )

    def __init__(
        self,
        module_id: str,
        registry: Registry,
        issuer: ContextIssuer,
        lifecycle: LifecycleController,
        repositories: SQLiteRepositories,
        authorization: AuthorizationService,
        caller_issuer: _IssuerCallerCapabilityIssuer,
        http: SourceHttpService,
        *,
        principal_resolver: InvocationPrincipalResolver,
        owner_authority: object | None = None,
        clock: Callable[[], float] = monotonic,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._module_id = module_id
        self._registry = registry
        self._issuer = issuer
        self._lifecycle = lifecycle
        self._repositories = repositories
        self._authorization = authorization
        if not callable(getattr(principal_resolver, "principal_id", None)):
            raise TypeError("module binder requires a principal resolver")
        self._principal_resolver = principal_resolver
        if owner_authority is not None and not callable(
            getattr(owner_authority, "require_current", None)
        ):
            raise TypeError("module binder owner authority is invalid")
        self._owner_authority = owner_authority
        self._caller_issuer = caller_issuer
        self._http = http
        self._clock = clock
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))

    def _check(
        self,
        invocation: InvocationView,
    ) -> RegisteredModule:
        try:
            view = self._issuer.require(invocation)
            if view.module_id != self._module_id:
                raise ValueError
            snapshot = self._registry.snapshot()
            module = snapshot.module(view.module_id)
            if not module.enabled or module.epoch != view.module_epoch:
                raise ValueError
            lease = self._issuer.lease_for(view)
            if view.origin is InvocationOrigin.SCHEDULER:
                if view.capability_id is not None or not isinstance(
                    lease, ScheduledLease
                ):
                    raise ValueError
            else:
                if (
                    view.origin
                    not in (
                        InvocationOrigin.COMMAND,
                        InvocationOrigin.LLM_TOOL,
                        InvocationOrigin.WEB_PUBLIC,
                    )
                    or view.capability_id is None
                    or not isinstance(lease, AdmissionLease)
                ):
                    raise ValueError
                descriptor = next(
                    (
                        item
                        for item in module.manifest.capabilities
                        if item.capability_id == view.capability_id
                    ),
                    None,
                )
                if descriptor is None:
                    raise ValueError
                if (
                    view.origin is InvocationOrigin.WEB_PUBLIC
                    and not self._issuer.allows_public_web(view.module_id, descriptor)
                ):
                    raise ValueError
                if view.origin is InvocationOrigin.LLM_TOOL and not tool_allowed(
                    descriptor
                ):
                    raise ValueError
            self._lifecycle.admission.check(lease)
            self._lifecycle.guard(
                view.module_id,
                epoch=view.module_epoch,
                registry_revision=view.registry_revision,
                invocation=view,
            )
            if view.subscription_id is not None:
                # B04 owns the subscription repository.  B03 cannot attest it.
                raise ValueError
            return module
        except Exception:
            raise InvocationBindingError() from None

    async def _admit(self, invocation: InvocationView) -> RegisteredModule:
        module = self._check(invocation)
        capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == invocation.capability_id
            ),
            None,
        )
        requires_grant = (
            capability is not None and capability.privacy_floor is PrivacyFloor.PRIVATE
        )
        if invocation.grant_id is not None or requires_grant:
            try:
                await self._require_invocation_grant(invocation)
            except Exception:
                raise InvocationBindingError() from None
        return module

    async def _require_invocation_grant(self, invocation: InvocationView) -> None:
        """Revalidate the grant through the authority for this exact origin."""

        if invocation.origin is InvocationOrigin.COMMAND:
            if invocation.parent_id is None:
                await self._authorization.require_current_grant(invocation)
            else:
                await self._authorization.require_current_derived_grant(invocation)
            return
        if invocation.origin is InvocationOrigin.SCHEDULER:
            lease = self._issuer.lease_for(invocation)
            if not isinstance(lease, ScheduledLease):
                raise ValueError
            await self._authorization.require_scheduled_grant(invocation, lease)
            return
        raise ValueError

    async def _resolve_principal(
        self, invocation: InvocationView, *, authorized: bool
    ) -> str:
        try:
            principal_id = await self._principal_resolver.principal_id(invocation)
            self._check(invocation)
            if authorized:
                await self._require_invocation_grant(invocation)
                self._check(invocation)
            return principal_id
        except Exception:
            raise InvocationBindingError() from None

    async def bind(self, invocation: InvocationView) -> _InvocationServices:
        module = await self._admit(invocation)
        caller_id = invocation.capability_id
        owner_capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == caller_id
            ),
            None,
        )
        is_owner = (
            owner_capability is not None
            and owner_capability.privacy_floor is PrivacyFloor.OWNER
        )
        if is_owner:
            if (
                invocation.grant_id is not None
                or invocation.grant_revision is not None
                or self._owner_authority is None
            ):
                raise InvocationBindingError()
            try:
                await self._owner_authority.require_current(invocation)
            except Exception:
                raise InvocationBindingError() from None
        is_scheduler = invocation.origin is InvocationOrigin.SCHEDULER
        if is_scheduler:
            caller_capability = None
            dependencies = None
        else:
            if caller_id is None:
                raise InvocationBindingError()
            caller_capability = self._caller_issuer.issue(invocation, caller_id)
            dependencies = DependencyInvoker(
                self._registry,
                self._issuer,
                self._lifecycle,
                self._caller_issuer,
                caller_capability,
                private_authorizer=self._authorization.require_dependency_grant,
                clock=self._clock,
                utc_clock=self._utc_clock,
            )

        def check_sync() -> None:
            try:
                self._check(invocation)
                if caller_capability is not None:
                    self._caller_issuer.require(invocation, caller_capability)
            except Exception:
                raise InvocationBindingError() from None

        async def check() -> None:
            check_sync()
            if is_owner:
                try:
                    await self._owner_authority.require_current(invocation)
                except Exception:
                    raise InvocationBindingError() from None
                check_sync()
            if invocation.grant_id is not None:
                await self._admit(invocation)
                check_sync()
            elif invocation.capability_id is not None and any(
                item.capability_id == invocation.capability_id
                and item.privacy_floor is PrivacyFloor.PRIVATE
                for item in module.manifest.capabilities
            ):
                await self._authorization.require_current_grant(invocation)
                check_sync()

        grant_store = self._repositories.authorization
        record_repository = self._repositories.records_for(module)

        async def collection_for_invocation(name: str) -> object:
            current_module = self._check(invocation)
            descriptor = next(
                (
                    item
                    for item in current_module.manifest.collections
                    if item.name == name
                ),
                None,
            )
            if descriptor is None:
                raise ValueError("collection is not declared for this module")
            if descriptor.owner_kind is OwnershipKind.PUBLIC:
                owner = OwnerScope.public()
            elif descriptor.owner_kind is OwnershipKind.USER:
                if invocation.actor_id is None:
                    raise InvocationBindingError()
                owner = OwnerScope.user(
                    await self._resolve_principal(invocation, authorized=False)
                )
            elif descriptor.owner_kind is OwnershipKind.AUTHORIZED:
                if invocation.actor_id is None or invocation.grant_id is None:
                    raise InvocationBindingError()
                principal_id = await self._resolve_principal(
                    invocation, authorized=True
                )
                owner = OwnerScope.authorized(
                    principal_id,
                    GrantReference(invocation.grant_id, invocation.grant_revision),
                )
            else:
                raise InvocationBindingError()
            scoped_records = ModuleRecordsService(
                current_module.module_id,
                record_repository,
                owner,
                self._repositories.registration_lookup,
            )
            return await scoped_records.collection(name)

        cache = CacheAccessService(
            invocation,
            self._repositories.cache,
            issuer=self._issuer,
            grant_store=grant_store,
            registration_lookup=self._repositories.registration_lookup,
            admission=self._lifecycle.admission,
            principal_resolver=self._principal_resolver,
            clock=self._clock,
        )
        resources = ResourceAccessService(
            invocation,
            self._repositories.resources,
            issuer=self._issuer,
            grant_store=grant_store,
            registration_lookup=self._repositories.registration_lookup,
            admission=self._lifecycle.admission,
            principal_resolver=self._principal_resolver,
            clock=self._clock,
        )
        tasks = self._lifecycle.scope(module.module_id)
        bound = _InvocationServices(
            invocation=invocation,
            records=_BoundRecords(collection_for_invocation, check_sync, check),
            cache=_BoundCache(cache, check_sync, check),
            http=_BoundHttp(self._http, check_sync, check),
            resources=_BoundResources(resources, check_sync, check),
            dependencies=_BoundDependencies(dependencies, check),
            tasks=_BoundTasks(tasks, check_sync, check),
        )
        await check()
        return bound


class ModuleServicesFactory:
    """Construct one module-scoped service bundle from explicit host inputs."""

    __slots__ = (
        "_registry",
        "_issuer",
        "_lifecycle",
        "_repositories",
        "_lookup",
        "_principal_resolver",
        "_owner_authority",
        "_config_principal_id",
        "_identity_namespace",
        "_http_transport",
        "_source_credential_policies",
        "_exchange_verifier",
        "_caller_issuer",
        "_http_by_module",
        "_credential_services",
        "_subscriptions",
        "_secret_available",
        "_clock",
        "_utc_clock",
        "_module_host_config_snapshots",
    )

    def __init__(
        self,
        registry: Registry,
        issuer: ContextIssuer,
        lifecycle: LifecycleController,
        repositories: SQLiteRepositories,
        http_transport: object,
        *,
        config_principal_id: str,
        identity_namespace: str,
        subscriptions: SubscriptionOperations | None = None,
        principal_resolver: InvocationPrincipalResolver | None = None,
        owner_authority: object | None = None,
        source_credential_policies: Sequence[SourceCredentialPolicy] = (),
        secret_available: SecretAvailability | None = None,
        exchange_verifier: Callable[..., object] | None = None,
        clock: Callable[[], float] = monotonic,
        utc_clock: Callable[[], datetime] | None = None,
        module_host_config_snapshots: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        if not isinstance(registry, Registry) or not isinstance(issuer, ContextIssuer):
            raise TypeError("module services require the host Registry and issuer")
        if not isinstance(lifecycle, LifecycleController):
            raise TypeError("module services require the host lifecycle")
        if lifecycle.issuer is not issuer:
            raise TypeError("lifecycle must use the same ContextIssuer")
        if not isinstance(repositories, SQLiteRepositories):
            raise TypeError("module services require SQLite repositories")
        if (
            not callable(getattr(http_transport, "request", None))
            and not callable(http_transport)
            and not callable(getattr(http_transport, "fetch", None))
        ):
            raise TypeError("HTTP transport must be explicitly injected")
        if type(config_principal_id) is not str or not config_principal_id.strip():
            raise ValueError("config principal is required")
        if type(identity_namespace) is not str or not identity_namespace.strip():
            raise ValueError("identity namespace is required")
        credential_policies = tuple(source_credential_policies)
        if any(
            not isinstance(item, SourceCredentialPolicy) for item in credential_policies
        ):
            raise TypeError("source credential policies must be validated values")
        if len(
            {(item.module_id, item.source_id) for item in credential_policies}
        ) != len(credential_policies):
            raise ValueError("source credential policies must be unique")
        if len(
            {(item.module_id, item.credential_ref) for item in credential_policies}
        ) != len(credential_policies):
            raise ValueError("source credential aliases must be unique per module")
        if exchange_verifier is not None and not callable(exchange_verifier):
            raise TypeError("exchange verifier must be callable")
        if secret_available is not None and not callable(secret_available):
            raise TypeError("secret availability checker must be callable")
        if subscriptions is not None and any(
            not callable(getattr(subscriptions, name, None))
            for name in (
                "create",
                "revise",
                "create_request",
                "revise_request",
                "list_current",
                "cancel",
            )
        ):
            raise TypeError("subscriptions must implement the SDK Protocol")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if utc_clock is not None and not callable(utc_clock):
            raise TypeError("UTC clock must be callable")
        if principal_resolver is None:
            principal_resolver = InvocationPrincipalResolver(
                issuer,
                repositories.identities,
                identity_namespace=identity_namespace,
                admission=lifecycle.admission,
            )
        if not callable(getattr(principal_resolver, "principal_id", None)):
            raise TypeError("module services require a principal resolver")
        if owner_authority is not None and not callable(
            getattr(owner_authority, "require_current", None)
        ):
            raise TypeError("module services owner authority is invalid")
        self._registry = registry
        self._issuer = issuer
        self._lifecycle = lifecycle
        self._repositories = repositories
        self._lookup = repositories.registration_lookup
        self._principal_resolver = principal_resolver
        self._owner_authority = owner_authority
        self._config_principal_id = config_principal_id
        self._identity_namespace = identity_namespace
        self._http_transport = http_transport
        self._source_credential_policies = credential_policies
        self._exchange_verifier = exchange_verifier
        self._caller_issuer = _IssuerCallerCapabilityIssuer(issuer, registry)
        self._http_by_module: dict[str, tuple[ModuleManifest, SourceHttpService]] = {}
        self._credential_services: list[
            tuple[str, ModuleManifest, SourceCredentialService]
        ] = []
        self._subscriptions = subscriptions
        self._secret_available = secret_available
        self._clock = clock
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._module_host_config_snapshots = _freeze_host_config_snapshots(
            module_host_config_snapshots
        )

    def for_module(self, module_id: str) -> ModuleServices:
        try:
            registered = self._registry.snapshot().module(module_id)
        except Exception:
            raise ValueError("module is not registered") from None
        return self._build_module(module_id, registered.manifest)

    def for_candidate(self, module_id: str, manifest: ModuleManifest) -> ModuleServices:
        """Build dormant candidate services without consulting Registry."""

        if not isinstance(manifest, ModuleManifest):
            raise TypeError("manifest must be a ModuleManifest")
        if (
            type(module_id) is not str
            or "/" not in module_id
            or module_id.rsplit("/", 1)[-1] != manifest.module_id
        ):
            raise ValueError("candidate module ID must match the manifest")
        return self._build_module(module_id, manifest)

    def _new_authorization(self) -> AuthorizationService:
        return AuthorizationService(
            self._repositories.authorization,
            self._repositories.authorization,
            self._issuer,
            revocation_coordinator=self._repositories.grant_revocation,
            identity_repository=self._repositories.identities,
            conversation_repository=self._repositories.conversations,
            module_lookup=self._lookup,
            identity_namespace=self._identity_namespace,
            principal_resolver=self._principal_resolver,
            secret_store=self._repositories.secret_store,
            secret_available=self._secret_available,
            exchange_verifier=self._exchange_verifier,
            admission=self._lifecycle.admission,
            now=self._utc_clock,
        )

    async def authorize_private(self, invocation: InvocationView):
        """Host callback for Gateway/DependencyInvoker private entries."""

        if not isinstance(invocation, InvocationView):
            raise InvocationBindingError()
        try:
            self._registry.snapshot().module(invocation.module_id)
        except Exception:
            raise InvocationBindingError() from None
        authorization = self._new_authorization()
        return await authorization.require_current_grant(invocation)

    async def close_credentials(self) -> None:
        """Clear every token cache before Core closes the secret store/database.

        This method never closes the shared HTTP transport; its runtime owner
        closes that separately after module work has stopped.
        """

        services = tuple(self._credential_services)
        for _, _, service in services:
            await service.close()
        self._credential_services.clear()

    async def retire_module_credentials(
        self, module_id: str, *, manifest: ModuleManifest
    ) -> None:
        """Retire one stopped manifest binding after a module replacement."""

        if type(module_id) is not str or not module_id.strip():
            raise ValueError("module ID is required to retire credentials")
        if not isinstance(manifest, ModuleManifest):
            raise TypeError("manifest is required to retire credentials")
        retained: list[tuple[str, ModuleManifest, SourceCredentialService]] = []
        for owner_id, owner_manifest, service in self._credential_services:
            if owner_id == module_id and owner_manifest == manifest:
                await service.close()
            else:
                retained.append((owner_id, owner_manifest, service))
        self._credential_services = retained
        cached = self._http_by_module.get(module_id)
        if cached is not None and cached[0] == manifest:
            self._http_by_module.pop(module_id, None)

    def _build_module(self, module_id: str, manifest: ModuleManifest) -> ModuleServices:
        fields: tuple[ConfigField, ...] = manifest.config_fields
        host_values = self._module_host_config_snapshots.get(module_id, {})
        if set(host_values) & {field.name for field in fields}:
            raise ValueError("host config conflicts with declared Core config fields")
        if fields:
            config = ConfigurationService(
                ConfigTarget(self._config_principal_id, module_id),
                fields,
                self._repositories.config,
                self._repositories.secret_store,
            )
        else:
            config = _EmptyConfigView(
                ConfigTarget(self._config_principal_id, module_id)
            )
        if host_values:
            config = _HostConfigView(config, host_values)
        identities = IdentityResolverService(
            self._repositories.identities,
            self._repositories.conversations,
            self._repositories.bindings,
            self._lookup,
            self._issuer,
            identity_namespace=self._identity_namespace,
            principal_resolver=self._principal_resolver,
        )
        bindings = AccountOperationsService(
            self._repositories.identities,
            self._repositories.conversations,
            self._repositories.bindings,
            self._lookup,
            self._issuer,
            identity_namespace=self._identity_namespace,
            admission=self._lifecycle.admission,
            principal_resolver=self._principal_resolver,
        )
        authorization = self._new_authorization()
        cached_http = self._http_by_module.get(module_id)
        if cached_http is None or cached_http[0] != manifest:
            credential_policies = tuple(
                policy
                for policy in self._source_credential_policies
                if policy.module_id == module_id
            )
            effective_sources = bind_source_credential_declarations(
                module_id, manifest.sources, fields, credential_policies
            )
            credential_service = None
            if any(source.credential_ref is not None for source in effective_sources):
                credential_service = SourceCredentialService(
                    module_id,
                    effective_sources,
                    fields,
                    credential_policies,
                    self._repositories.config,
                    self._repositories.secret_store,
                    self._http_transport,
                    config_principal_id=self._config_principal_id,
                    clock=self._clock,
                )
                self._credential_services.append(
                    (module_id, manifest, credential_service)
                )
            http = SourceHttpService(
                effective_sources,
                self._http_transport,
                module_id=module_id,
                credential_service=credential_service,
                clock=self._clock,
            )
            self._http_by_module[module_id] = (manifest, http)
        else:
            http = cached_http[1]
        binder = InvocationServiceBinder(
            module_id,
            self._registry,
            self._issuer,
            self._lifecycle,
            self._repositories,
            authorization,
            self._caller_issuer,
            http,
            principal_resolver=self._principal_resolver,
            owner_authority=self._owner_authority,
            clock=self._clock,
            utc_clock=self._utc_clock,
        )
        subscription_delegate: SubscriptionOperations = self._subscriptions
        if subscription_delegate is None:
            subscription_delegate = UnavailableSubscriptionOperations(
                self._issuer, module_id
            )
        subscriptions = _BoundSubscriptionOperations(
            self._issuer,
            self._registry,
            self._lifecycle,
            module_id,
            subscription_delegate,
            self._owner_authority,
        )
        return ModuleServices(
            config=config,
            identities=_BoundIdentityResolver(
                self._issuer, self._registry, self._lifecycle, module_id, identities
            ),
            accounts=_BoundAccountOperations(
                self._issuer,
                self._registry,
                self._lifecycle,
                module_id,
                _AccountOperations(bindings, authorization),
            ),
            subscriptions=subscriptions,
            scopes=binder,
        )


def _freeze_host_config_snapshots(
    snapshots: Mapping[str, Mapping[str, object]] | None,
) -> Mapping[str, Mapping[str, object]]:
    """Copy host-owned JSON values without persisting them in Core storage."""
    if snapshots is None:
        return MappingProxyType({})
    if not isinstance(snapshots, Mapping):
        raise TypeError("module host config snapshots must be a mapping")
    frozen = {}
    for module_id, values in snapshots.items():
        target = ConfigTarget("host-config", module_id)
        if not isinstance(values, Mapping):
            raise TypeError("module host config values must be a mapping")
        frozen[module_id] = ConfigSnapshot(1, values, target=target).values
    return MappingProxyType(frozen)


class _HostConfigView:
    """Read-only composition; revision and secret metadata remain Core-owned."""

    __slots__ = ("_core", "_host_values")

    def __init__(self, core, host_values: Mapping[str, object]) -> None:
        self._core = core
        self._host_values = host_values

    async def current(self) -> ConfigSnapshot:
        core = await self._core.current()
        if set(core.values) & set(self._host_values):
            raise ValueError("host config conflicts with Core config values")
        return ConfigSnapshot(
            core.revision,
            {**core.values, **self._host_values},
            secret_metadata=core.secret_metadata,
            target=core.target,
        )


class _EmptyConfigView:
    """Read-only empty config view for modules without declared config fields."""

    __slots__ = ("_target",)

    def __init__(self, target: ConfigTarget) -> None:
        self._target = target

    async def current(self) -> ConfigSnapshot:
        return ConfigSnapshot(1, {}, target=self._target)


__all__ = [
    "InvocationBindingError",
    "InvocationServiceBinder",
    "ModuleServicesFactory",
    "RegistryRegistrationLookup",
    "UnavailableSubscriptionOperations",
]
