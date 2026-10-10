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
from uuid import uuid4
from weakref import WeakSet

from yomihime_game_link_sdk.contexts import (
    InvocationOrigin,
    InvocationView,
    MessageContext,
)
from yomihime_game_link_sdk.declarations import (
    ConfigField,
    ModuleManifest,
    PrivacyFloor,
)
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    ParameterError,
    ServiceUnavailable,
)
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from yomihime_game_link_sdk.services import (
    AccountOperations,
    BindingView,
    CallerCapability,
    ConfigSnapshot,
    ConfigTarget,
    HttpRequest,
    HttpResponse,
    InvocationServices,
    ModuleServices,
    ResolvedIdentity,
    ResourceReference,
    SubscriptionOperations,
)
from yomihime_game_link_sdk.storage import (
    CacheEntry,
    CacheLookup,
    CacheQuery,
    DeclaredIndexQuery,
    GrantReference,
    OwnerScope,
    OwnershipKind,
    QueryOperator,
    RecordPage,
    VersionedRecord,
)
from yomihime_game_link_sdk.subscriptions import SubscriptionRequest, SubscriptionView

from ..core.context_issuer import ContextIssuer
from ..core.contracts.storage import _record_cursor_offset
from ..core.contracts.validation_boundary import validate_contract
from ..core.lifecycle import LifecycleController, _ServiceLifetime
from ..core.policy import tool_allowed
from ..core.ports import (
    AdmissionLease,
    CallerCapabilityIssuer,
    ModuleNotRegistered,
    ModuleRegistrationLookup,
    ModuleRegistrationSnapshot,
    ScheduledLease,
)
from ..core.public_errors import parameters, proof, public_boundary
from ..core.registry import RegisteredModule, Registry
from ..core.task_scope import TaskScope
from ..infrastructure.http import SourceHttpError, SourceHttpService
from ..infrastructure.sqlite.repositories import SQLiteRepositories
from .authorization import AuthorizationService, SecretAvailability
from .bindings import AccountOperationsService
from .cache import CacheAccessService, ModuleCacheRepository
from .configuration import ConfigurationCoordinator
from .core_configuration import (
    CORE_DEFAULTS_FIELD,
    CORE_MODULE_ID,
    CoreDefaultsConfigView,
    CoreDefaultsView,
)
from .dependency_calls import DependencyInvoker
from .identity import IdentityResolverService, InvocationPrincipalResolver
from .module_storage import ModuleStorageRouter
from .records import ModuleRecordsService, _collection_name
from .resources import ResourceAccessService, _asset_id
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
        validate_contract(view)
        for key in tuple(self._issued):
            if key[0] == view.invocation_id:
                self._issued.pop(key, None)

    @public_boundary("proof")
    def issue(self, invocation: InvocationView, capability_id: str) -> CallerCapability:
        validate_contract(invocation)
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
            caller = validate_contract(
                CallerCapability(
                    view.module_id, capability_id, view.registry_revision, module.epoch
                )
            )
        except Exception:
            raise
        self._issued[(view.invocation_id, capability_id)] = caller
        return caller

    @public_boundary("proof")
    def require(
        self, invocation: InvocationView, caller: CallerCapability
    ) -> CallerCapability:
        validate_contract(invocation)
        validate_contract(caller)
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
            raise
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
        validate_contract(invocation)
        view = self._issuer.require(invocation)
        if (
            view.module_id != self._module_id
            or view.origin is not InvocationOrigin.COMMAND
            or view.parent_id is not None
        ):
            raise PermissionError("subscription operation is not permitted")

    async def create_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        validate_contract(invocation)
        validate_contract(request)
        self._command(invocation)
        raise ServiceUnavailable()

    async def revise_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        validate_contract(invocation)
        validate_contract(request)
        self._command(invocation)
        raise ServiceUnavailable()

    async def list_current(
        self, invocation: InvocationView
    ) -> tuple[SubscriptionView, ...]:
        validate_contract(invocation)
        self._command(invocation)
        raise ServiceUnavailable()

    async def cancel(
        self,
        invocation: InvocationView,
        subscription_id: str,
        *,
        expected_revision: int,
    ) -> None:
        validate_contract(invocation)
        self._command(invocation)
        raise ServiceUnavailable()


@public_boundary("proof")
def _require_command_only_entry(
    issuer: ContextIssuer,
    registry: Registry,
    lifecycle: LifecycleController,
    module_id: str,
    invocation: InvocationView,
) -> RegisteredModule:
    validate_contract(invocation)
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
        raise


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
        validate_contract(delegate)
        self._issuer = issuer
        self._registry = registry
        self._lifecycle = lifecycle
        self._module_id = module_id
        self._delegate = delegate
        self._owner_authority = owner_authority

    def _command(self, invocation: InvocationView) -> RegisteredModule:
        proof(validate_contract, invocation)
        return _require_command_only_entry(
            self._issuer,
            self._registry,
            self._lifecycle,
            self._module_id,
            invocation,
        )

    async def _owner_check(self, invocation: InvocationView) -> None:
        proof(validate_contract, invocation)
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
            raise
        self._command(invocation)

    @public_boundary("service")
    async def create_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        proof(validate_contract, invocation)
        await self._owner_check(invocation)
        parameters(_canonical_input, request, SubscriptionRequest)
        result = await self._delegate.create_request(invocation, request)
        await self._owner_check(invocation)
        _canonical_input(result, SubscriptionView)
        return result

    @public_boundary("service")
    async def revise_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        proof(validate_contract, invocation)
        await self._owner_check(invocation)
        parameters(_canonical_input, request, SubscriptionRequest)
        result = await self._delegate.revise_request(invocation, request)
        await self._owner_check(invocation)
        _canonical_input(result, SubscriptionView)
        return result

    @public_boundary("service")
    async def list_current(
        self, invocation: InvocationView
    ) -> tuple[SubscriptionView, ...]:
        proof(validate_contract, invocation)
        await self._owner_check(invocation)
        result = await self._delegate.list_current(invocation)
        await self._owner_check(invocation)
        if type(result) is not tuple:
            raise ValueError("subscription response is invalid")
        for item in result:
            _canonical_input(item, SubscriptionView)
        return result

    @public_boundary("service")
    async def cancel(
        self,
        invocation: InvocationView,
        subscription_id: str,
        *,
        expected_revision: int,
    ) -> None:
        proof(validate_contract, invocation)
        await self._owner_check(invocation)
        parameters(
            _record_input,
            "delete",
            (subscription_id,),
            {"expected_revision": expected_revision},
        )
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
        validate_contract(invocation)
        return await self._authorization.status(invocation)

    async def begin_login(self, invocation: InvocationView) -> str:
        validate_contract(invocation)
        return await self._authorization.begin_login(invocation)

    async def bind(
        self, invocation: InvocationView, identity: ResolvedIdentity
    ) -> BindingView:
        validate_contract(invocation)
        validate_contract(identity)
        return await self._bindings.bind(invocation, identity)

    async def bindings(self, invocation: InvocationView) -> tuple[BindingView, ...]:
        validate_contract(invocation)
        return await self._bindings.bindings(invocation)

    async def unbind(
        self, invocation: InvocationView, binding_id: str, *, expected_revision: int
    ) -> None:
        validate_contract(invocation)
        await self._bindings.unbind(
            invocation, binding_id, expected_revision=expected_revision
        )

    async def revoke(self, invocation: InvocationView, grant: GrantReference) -> None:
        validate_contract(invocation)
        validate_contract(grant)
        await self._authorization.revoke(invocation, grant)


class _BoundIdentityResolver:
    __slots__ = ("_issuer", "_registry", "_lifecycle", "_module_id", "_delegate")

    def __init__(self, issuer, registry, lifecycle, module_id, delegate) -> None:
        self._issuer = issuer
        self._registry = registry
        self._lifecycle = lifecycle
        self._module_id = module_id
        self._delegate = delegate

    @public_boundary("service")
    async def default_identity(self, invocation: InvocationView):
        proof(validate_contract, invocation)
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        result = await self._delegate.default_identity(invocation)
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        if result is not None:
            _canonical_input(result, ResolvedIdentity)
        return result


class _BoundAccountOperations(AccountOperations):
    __slots__ = ("_issuer", "_registry", "_lifecycle", "_module_id", "_delegate")

    def __init__(self, issuer, registry, lifecycle, module_id, delegate) -> None:
        self._issuer = issuer
        self._registry = registry
        self._lifecycle = lifecycle
        self._module_id = module_id
        self._delegate = delegate

    @public_boundary("service")
    async def _call(self, name: str, invocation: InvocationView, *args, **kwargs):
        proof(validate_contract, invocation)
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        result = await getattr(self._delegate, name)(invocation, *args, **kwargs)
        _require_command_only_entry(
            self._issuer, self._registry, self._lifecycle, self._module_id, invocation
        )
        _account_output(name, result)
        return result

    async def status(self, invocation: InvocationView):
        proof(validate_contract, invocation)
        return await self._call("status", invocation)

    async def begin_login(self, invocation: InvocationView):
        proof(validate_contract, invocation)
        return await self._call("begin_login", invocation)

    @public_boundary("service")
    async def bind(self, invocation: InvocationView, identity: ResolvedIdentity):
        proof(validate_contract, invocation)
        parameters(_canonical_input, identity, ResolvedIdentity)
        return await self._call("bind", invocation, identity)

    async def bindings(self, invocation: InvocationView):
        proof(validate_contract, invocation)
        return await self._call("bindings", invocation)

    async def unbind(
        self, invocation: InvocationView, binding_id: str, *, expected_revision: int
    ) -> None:
        proof(validate_contract, invocation)
        parameters(
            _record_input,
            "delete",
            (binding_id,),
            {"expected_revision": expected_revision},
        )
        await self._call(
            "unbind", invocation, binding_id, expected_revision=expected_revision
        )

    @public_boundary("service")
    async def revoke(self, invocation: InvocationView, grant: GrantReference) -> None:
        proof(validate_contract, invocation)
        parameters(_canonical_input, grant, GrantReference)
        await self._call("revoke", invocation, grant)


def _canonical_input(value, expected):
    if type(value) is not expected:
        raise TypeError("input must be canonical")
    validate_contract(value)


def _account_output(name, result):
    expected = {"status": GrantReference, "bind": BindingView, "bindings": BindingView}
    if name == "begin_login":
        if type(result) is not str or not result.strip():
            raise ValueError("login response is invalid")
    elif name == "bindings":
        if type(result) is not tuple:
            raise ValueError("binding response is invalid")
        for item in result:
            _canonical_input(item, BindingView)
    elif name in expected:
        if name != "status" or result is not None:
            _canonical_input(result, expected[name])
    elif name not in expected and result is not None:
        raise ValueError("operation response is invalid")


def _record_input(method, args, kwargs):
    def key_input(key):
        if (
            type(key) is not str
            or not key.strip()
            or any(c in key for c in ("/", "\\", "\n", "\r"))
        ):
            raise ValueError("record key is invalid")

    def revision_input(revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("record revision is invalid")

    def get(key):
        key_input(key)

    def create(key, value):
        key_input(key)
        validate_contract(VersionedRecord(key, 1, value))

    def replace(key, value, *, expected_revision):
        revision_input(expected_revision)
        create(key, value)

    def query(query):
        if type(query) is not DeclaredIndexQuery:
            raise TypeError("query must be canonical")
        validate_contract(query)
        if query.operator is QueryOperator.PREFIX and type(query.value) is not str:
            raise ValueError("prefix query requires text")
        if query.cursor is not None and type(query.cursor) is not str:
            raise ValueError("query cursor is invalid")
        _record_cursor_offset(query.cursor)

    def delete(key, *, expected_revision):
        key_input(key)
        revision_input(expected_revision)

    {
        "get": get,
        "create": create,
        "replace": replace,
        "query": query,
        "delete": delete,
    }[method](*args, **kwargs)


def _record_output(method, result):
    if method == "delete":
        if result is not None:
            raise ValueError("record deletion returned invalid data")
    elif method == "get" and result is None:
        return
    elif type(result) is not (RecordPage if method == "query" else VersionedRecord):
        raise ValueError("record operation returned invalid data")
    else:
        validate_contract(result)


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
        if name == "scope":

            @public_boundary()
            def current_scope():
                self.__check_sync()
                result = getattr(self.__collection, name)
                if type(result) is not OwnerScope:
                    raise ValueError("record scope is invalid")
                validate_contract(result)
                return result

            return current_scope()
        target = getattr(self.__collection, name)
        if not callable(target):
            raise AttributeError(name)

        @public_boundary()
        async def invoke(*args: object, **kwargs: object) -> object:
            await self.__check()
            parameters(_record_input, name, args, kwargs)
            result = await target(*args, **kwargs)
            await self.__check()
            _record_output(name, result)
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

    @public_boundary("service")
    async def collection(self, name: str) -> _BoundRecordCollection:
        await self.__check()
        parameters(_collection_name, name)
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

    @public_boundary("service")
    async def get(self, key: str) -> CacheEntry | None:
        await self.__check()
        result = await self.__cache.get(key)
        await self.__check()
        return result

    @public_boundary("service")
    async def lookup(self, request: CacheQuery) -> CacheLookup:
        await self.__check()
        result = await self.__cache.lookup(request)
        await self.__check()
        return result

    @public_boundary("service")
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

    @public_boundary("resource")
    async def register(self, asset_id: str, media_type: str, content: bytes):
        await self.__check()
        parameters(_asset_id, asset_id)
        if (
            type(content) is not bytes
            or type(media_type) is not str
            or not media_type.strip()
            or "\n" in media_type
            or "\r" in media_type
        ):
            raise ParameterError("parameters are invalid")
        result = await self.__resources.register(asset_id, media_type, content)
        await self.__check()
        _canonical_input(result, ResourceReference)
        return result

    @public_boundary("resource")
    async def read(self, asset_id: str) -> bytes:
        await self.__check()
        parameters(_asset_id, asset_id)
        result = await self.__resources.read(asset_id)
        await self.__check()
        if type(result) is not bytes:
            raise ValueError("resource response is invalid")
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

    @public_boundary("service")
    async def fetch(self, request: HttpRequest) -> HttpResponse:
        parameters(_canonical_input, request, HttpRequest)
        try:
            await self.__check()
        except (InvocationBindingError, AccessDenied):
            raise SourceHttpError("request_rejected") from None
        response = await self.__http.fetch(request)
        try:
            await self.__check()
        except (InvocationBindingError, AccessDenied):
            raise SourceHttpError("request_rejected") from None
        _canonical_input(response, HttpResponse)
        return response


class _BoundTasks:
    __slots__ = ("__scope", "__check_sync", "__check")

    def __init__(
        self,
        scope: TaskScope,
        check_sync: Callable[[], None],
        check: Callable[[], Awaitable[None]],
    ) -> None:
        validate_contract(scope)
        object.__setattr__(self, "_BoundTasks__scope", scope)
        object.__setattr__(self, "_BoundTasks__check_sync", check_sync)
        object.__setattr__(self, "_BoundTasks__check", check)

    @public_boundary("invocation")
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

    @public_boundary("invocation")
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
    @public_boundary("proof")
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
        validate_contract(dependencies)
        object.__setattr__(self, "_BoundDependencies__dependencies", dependencies)
        object.__setattr__(self, "_BoundDependencies__check", check)

    @property
    @public_boundary("proof")
    def caller_capability(self) -> CallerCapability:
        if self.__dependencies is None:
            raise InvocationBindingError()
        return self.__dependencies.caller_capability

    @public_boundary("service")
    async def invoke(
        self, invocation: InvocationView, capability: object, parameters: object
    ):
        proof(validate_contract, invocation)
        await self.__check()
        if self.__dependencies is None:
            return validate_contract(
                CapabilityResult(
                    "dependency-unavailable",
                    ResultStatus.ERROR,
                    error=validate_contract(
                        ErrorDetail(
                            ErrorCode.UNSUPPORTED,
                            "dependency invocation is unavailable for this context",
                        )
                    ),
                )
            )
        result = await self.__dependencies.invoke(invocation, capability, parameters)
        await self.__check()
        return result


class _BoundMessage:
    """No source objects escape the Core-owned exact invocation sidecar."""

    __slots__ = ("__issuer", "__view", "__check_sync", "__check")

    def __init__(self, issuer, view, check_sync, check):
        self.__issuer, self.__view = issuer, view
        self.__check_sync, self.__check = check_sync, check

    @public_boundary("proof")
    async def read(self) -> MessageContext:
        self.__check_sync()
        await self.__check()
        await self.__issuer._check_message_source(self.__view)
        await self.__check()
        # Build the descriptive copy before the final current-source check.
        # No await or user hook follows the synchronous issuer/lease/binder and
        # source checks below, so an old async True cannot authorize this read.
        snapshot = self.__issuer._message_for(self.__view).snapshot
        result = MessageContext(snapshot.text, snapshot.event_ref)
        self.__check_sync()
        self.__issuer._require_message_current(self.__view)
        return result


@dataclass(frozen=True, slots=True)
class _InvocationServices(InvocationServices):
    invocation: InvocationView
    message: _BoundMessage
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

    @public_boundary("proof")
    def _check(
        self,
        invocation: InvocationView,
    ) -> RegisteredModule:
        proof(validate_contract, invocation)
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
            raise

    @public_boundary("proof")
    async def _admit(self, invocation: InvocationView) -> RegisteredModule:
        proof(validate_contract, invocation)
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
                raise
        return module

    @public_boundary("proof")
    async def _require_invocation_grant(self, invocation: InvocationView) -> None:
        """Revalidate the grant through the authority for this exact origin."""

        proof(validate_contract, invocation)
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

    @public_boundary("service")
    async def _resolve_principal(
        self, invocation: InvocationView, *, authorized: bool
    ) -> str:
        proof(validate_contract, invocation)
        try:
            principal_id = await self._principal_resolver.principal_id(invocation)
            self._check(invocation)
            if authorized:
                await self._require_invocation_grant(invocation)
                self._check(invocation)
            return principal_id
        except Exception:
            raise

    @public_boundary("service")
    async def bind(self, invocation: InvocationView) -> _InvocationServices:
        proof(validate_contract, invocation)
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
                raise
        is_scheduler = invocation.origin is InvocationOrigin.SCHEDULER
        if is_scheduler:
            caller_capability = None
            dependencies = None
        else:
            if caller_id is None:
                raise InvocationBindingError()
            caller_capability = self._caller_issuer.issue(invocation, caller_id)
            dependencies = validate_contract(
                DependencyInvoker(
                    self._registry,
                    self._issuer,
                    self._lifecycle,
                    self._caller_issuer,
                    caller_capability,
                    private_authorizer=self._authorization.require_dependency_grant,
                    clock=self._clock,
                    utc_clock=self._utc_clock,
                )
            )

        @public_boundary("proof")
        def check_sync() -> None:
            try:
                self._check(invocation)
                if caller_capability is not None:
                    self._caller_issuer.require(invocation, caller_capability)
            except Exception:
                raise

        @public_boundary("proof")
        async def check() -> None:
            check_sync()
            if is_owner:
                try:
                    await self._owner_authority.require_current(invocation)
                except Exception:
                    raise
                check_sync()
            await self._admit(invocation)
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
                raise ParameterError("parameters are invalid")
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
                    validate_contract(
                        GrantReference(invocation.grant_id, invocation.grant_revision)
                    ),
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
            ModuleCacheRepository(self._repositories.cache, invocation.module_id),
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
            message=_BoundMessage(self._issuer, invocation, check_sync, check),
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
        "_service_lifetimes",
        "_services_closed",
        "_credential_services",
        "_subscriptions",
        "_secret_available",
        "_clock",
        "_utc_clock",
        "_module_host_config_snapshots",
        "_core_defaults",
        "_module_config_validators",
        "_storage_router",
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
        core_defaults: CoreDefaultsView | None = None,
        module_config_validators: Mapping[str, Mapping[str, object]] | None = None,
        storage_router: ModuleStorageRouter | None = None,
    ) -> None:
        validate_contract(subscriptions)
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
        self._storage_router = storage_router
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
        self._service_lifetimes: WeakSet[_ServiceLifetime] = WeakSet()
        self._services_closed = False
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
        self._core_defaults = core_defaults
        self._module_config_validators = MappingProxyType(
            {
                module_id: MappingProxyType(dict(checks))
                for module_id, checks in (module_config_validators or {}).items()
            }
        )

    def for_module(self, module_id: str) -> ModuleServices:
        try:
            registered = self._registry.snapshot().module(module_id)
        except Exception:
            raise ValueError("module is not registered") from None
        return self._build_module(
            module_id, registered.manifest, self._lifecycle.service_lifetime(module_id)
        )

    def for_candidate(
        self,
        module_id: str,
        manifest: ModuleManifest,
        *,
        service_lifetime: _ServiceLifetime | None = None,
    ) -> ModuleServices:
        """Build dormant candidate services without consulting Registry."""

        validate_contract(manifest)
        if not isinstance(manifest, ModuleManifest):
            raise TypeError("manifest must be a ModuleManifest")
        if (
            type(module_id) is not str
            or "/" not in module_id
            or module_id.rsplit("/", 1)[-1] != manifest.module_id
        ):
            raise ValueError("candidate module ID must match the manifest")
        if service_lifetime is None:
            service_lifetime = _ServiceLifetime(
                (module_id.split("/")[0], module_id, uuid4().hex)
            )
        return self._build_module(module_id, manifest, service_lifetime)

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

        validate_contract(invocation)
        if not isinstance(invocation, InvocationView):
            raise InvocationBindingError()
        try:
            self._registry.snapshot().module(invocation.module_id)
        except Exception:
            raise
        authorization = self._new_authorization()
        return await authorization.require_current_grant(invocation)

    async def close_credentials(self) -> None:
        """Clear every token cache before Core closes the secret store/database.

        This method never closes the shared HTTP transport; its runtime owner
        closes that separately after module work has stopped.
        """

        self._services_closed = True
        for lifetime in tuple(self._service_lifetimes):
            lifetime.revoke()
        services = tuple(self._credential_services)
        for _, _, service in services:
            await service.close()
        self._credential_services.clear()

    async def retire_module_credentials(
        self, module_id: str, *, manifest: ModuleManifest
    ) -> None:
        """Retire one stopped manifest binding after a module replacement."""

        validate_contract(manifest)
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

    def _build_module(
        self,
        module_id: str,
        manifest: ModuleManifest,
        service_lifetime: _ServiceLifetime,
    ) -> ModuleServices:
        if self._services_closed:
            raise ServiceUnavailable()
        service_lifetime.check()
        self._service_lifetimes.add(service_lifetime)
        validate_contract(manifest)
        fields: tuple[ConfigField, ...] = manifest.config_fields
        if module_id == CORE_MODULE_ID or any(
            field.name in {CORE_DEFAULTS_FIELD, "default_region"} for field in fields
        ):
            raise ValueError("module config target or field is reserved by Core")
        host_values = self._module_host_config_snapshots.get(module_id, {})
        if {CORE_DEFAULTS_FIELD, "default_region"} & set(host_values):
            raise ValueError("host config contains a reserved Core defaults field")
        if set(host_values) & {field.name for field in fields}:
            raise ValueError("host config conflicts with declared Core config fields")
        if fields:
            config = ConfigurationCoordinator(
                validate_contract(ConfigTarget(self._config_principal_id, module_id)),
                fields,
                self._repositories.config,
                self._repositories.secret_store,
                value_validators=self._module_config_validators.get(module_id),
            ).view()
        else:
            config = _EmptyConfigView(
                validate_contract(ConfigTarget(self._config_principal_id, module_id))
            )
        if host_values:
            config = _HostConfigView(config, host_values)
        if self._core_defaults is not None:
            config = CoreDefaultsConfigView(
                config, self._core_defaults, self._lifecycle.admission
            )
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
        binder = validate_contract(
            InvocationServiceBinder(
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
        bundle = validate_contract(
            ModuleServices(
                config=_PublicConfigView(config),
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
        )
        return validate_contract(
            ModuleServices(
                config=_LifetimePort(bundle.config, service_lifetime, {"current"}),
                identities=_LifetimePort(
                    bundle.identities, service_lifetime, {"default_identity"}
                ),
                accounts=_LifetimePort(
                    bundle.accounts,
                    service_lifetime,
                    {"status", "begin_login", "bind", "bindings", "unbind", "revoke"},
                ),
                subscriptions=_LifetimePort(
                    bundle.subscriptions,
                    service_lifetime,
                    {"create_request", "revise_request", "list_current", "cancel"},
                ),
                scopes=_LifetimePort(bundle.scopes, service_lifetime, {"bind"}),
            )
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
        target = validate_contract(ConfigTarget("host-config", module_id))
        if not isinstance(values, Mapping):
            raise TypeError("module host config values must be a mapping")
        frozen[module_id] = validate_contract(
            ConfigSnapshot(1, values, target=target)
        ).values
    return MappingProxyType(frozen)


class _LifetimePort:
    """Existing SDK operations fenced by their exact Core instance lifetime."""

    __slots__ = ("__delegate", "__lifetime", "__methods")

    def __init__(self, delegate, lifetime, methods):
        self.__delegate = delegate
        self.__lifetime = lifetime
        self.__methods = frozenset(methods)

    def __getattr__(self, name):
        if name not in self.__methods:
            raise AttributeError(name)
        target = getattr(self.__delegate, name)

        @public_boundary("service")
        async def invoke(*args, **kwargs):
            self.__lifetime.check()
            result = await target(*args, **kwargs)
            self.__lifetime.check()
            return result

        return invoke


class _PublicConfigView:
    __slots__ = ("_delegate",)

    def __init__(self, delegate):
        self._delegate = delegate

    @public_boundary("service")
    async def current(self):
        result = await self._delegate.current()
        _canonical_input(result, ConfigSnapshot)
        return result


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
        return validate_contract(
            ConfigSnapshot(
                core.revision,
                {**core.values, **self._host_values},
                secret_metadata=core.secret_metadata,
                target=core.target,
            )
        )


class _EmptyConfigView:
    """Read-only empty config view for modules without declared config fields."""

    __slots__ = ("_target",)

    def __init__(self, target: ConfigTarget) -> None:
        validate_contract(target)
        self._target = target

    async def current(self) -> ConfigSnapshot:
        return validate_contract(ConfigSnapshot(1, {}, target=self._target))


__all__ = [
    "InvocationBindingError",
    "InvocationServiceBinder",
    "ModuleServicesFactory",
    "RegistryRegistrationLookup",
    "UnavailableSubscriptionOperations",
]
