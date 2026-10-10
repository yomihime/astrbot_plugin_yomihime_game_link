"""Controlled invocation of capabilities declared as module dependencies.

This adapter deliberately stays below the public ``DependencyInvoker`` port:
the port is a small protocol, while the registry, issuer and lifecycle objects
are trusted runtime authorities supplied by the assembler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityReference,
    PrivacyFloor,
)
from yomihime_game_link_sdk.display import Privacy
from yomihime_game_link_sdk.errors import ParameterError
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from yomihime_game_link_sdk.services import CallerCapability

from ..core.context_issuer import ContextIssuer, InvalidInvocation
from ..core.contracts.manifests import CapabilityReference_validate
from ..core.contracts.services import Grant, GrantStatus, JsonObject
from ..core.contracts.validation import validate_parameters
from ..core.contracts.validation_boundary import validate_contract
from ..core.lifecycle import LifecycleController, LifecycleError
from ..core.policy import supports_public_read_only, tool_allowed
from ..core.ports import CallerCapabilityIssuer
from ..core.registry import RegisteredModule, Registry, RegistryError, RegistrySnapshot
from ..core.task_scope import (
    ScopeCancelled,
    ScopeDeadlineExceeded,
    ScopeStaleError,
)

_ERROR_MESSAGES = {
    ErrorCode.PARAMETER_ERROR: "invalid parameters",
    ErrorCode.NOT_FOUND: "invocation target is not registered",
    ErrorCode.UNSUPPORTED: "invocation is unsupported",
    ErrorCode.MODULE_UNAVAILABLE: "module is unavailable",
    ErrorCode.UNKNOWN: "invocation failed",
}
_ACTIVE_CHAINS: dict[str, tuple[tuple[str, str], ...]] = {}


def _error(code: ErrorCode) -> CapabilityResult:
    validate_contract(code)
    return validate_contract(
        CapabilityResult(
            result_id="dependency-error",
            status=ResultStatus.ERROR,
            error=validate_contract(ErrorDetail(code, _ERROR_MESSAGES[code])),
        )
    )


class DependencyCallError(RuntimeError):
    """Base class for dependency admission failures.

    Public callers receive a stable ``CapabilityResult``; these exceptions
    remain useful to a test or assembler that needs to distinguish a rejected
    call internally without exposing implementation details to a module.
    """


class DependencyInvoker:
    """Execute only currently declared, lifecycle-admitted dependencies.

    Legacy string dependencies remain local to the caller module.  A
    ``CapabilityReference`` is required for a cross-module target, so identical
    capability IDs in two modules cannot be confused.
    """

    def __init__(
        self,
        registry: Registry,
        issuer: ContextIssuer,
        lifecycle: LifecycleController,
        caller_issuer: CallerCapabilityIssuer,
        caller_capability: CallerCapability,
        *,
        private_authorizer: Callable[
            [InvocationView, InvocationView], Awaitable[object]
        ]
        | None = None,
        max_depth: int = 8,
        clock=monotonic,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        validate_contract(caller_capability)
        validate_contract(private_authorizer)
        if not isinstance(registry, Registry):
            raise TypeError("registry must be a Registry")
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("issuer must be a ContextIssuer")
        if not isinstance(lifecycle, LifecycleController):
            raise TypeError("lifecycle must be a LifecycleController")
        if not callable(getattr(caller_issuer, "issue", None)) or not callable(
            getattr(caller_issuer, "require", None)
        ):
            raise TypeError("caller_issuer must provide issue and require")
        if not isinstance(caller_capability, CallerCapability):
            raise TypeError("caller_capability must be a CallerCapability")
        if private_authorizer is not None and not callable(private_authorizer):
            raise TypeError("private_authorizer must be callable")
        if (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 1
        ):
            raise ValueError("max_depth must be a positive integer")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if utc_clock is not None and not callable(utc_clock):
            raise TypeError("UTC clock must be callable")
        self._registry = registry
        self._issuer = issuer
        self._lifecycle = lifecycle
        self._caller_issuer = caller_issuer
        self._caller_capability = caller_capability
        self._private_authorizer = private_authorizer
        self._max_depth = max_depth
        self._clock = clock
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))

    async def invoke(
        self,
        invocation: InvocationView,
        capability: str | CapabilityReference,
        parameters: JsonObject,
    ) -> CapabilityResult:
        """Invoke one declared dependency and return only a data result.

        The caller's invocation is the only source of provenance.  Actor,
        grant, subscription and origin cannot be supplied by the parameters or
        by the target declaration.
        """

        validate_contract(invocation)
        validate_contract(capability)
        validate_contract(parameters)
        child: InvocationView | None = None
        child_lease: object | None = None
        private_grant: Grant | None = None
        try:
            parent = self._issuer.require(invocation)
            parent_lease = self._issuer.lease_for(parent)
            if parent_lease is None:
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            self._lifecycle.admission.check(parent_lease)  # type: ignore[arg-type]
            try:
                caller = self._caller_issuer.require(parent, self._caller_capability)
            except Exception:
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            if not isinstance(caller, CallerCapability):
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            if (
                caller.module_id != parent.module_id
                or caller.registry_revision != parent.registry_revision
                or caller.module_epoch != parent.module_epoch
            ):
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            snapshot, source = self._current_source(parent)
            self._lifecycle.guard(
                parent.module_id,
                epoch=parent.module_epoch,
                registry_revision=parent.registry_revision,
                invocation=parent,
            )
            descriptor = self._caller_descriptor(source, caller)
            target_ref = self._target_reference(parent, capability)
            if descriptor is None or not self._dependency_declared(
                descriptor, target_ref, caller.module_id
            ):
                return _error(ErrorCode.UNSUPPORTED)
            target, target_descriptor = self._resolve_target(snapshot, target_ref)
            if target is None or target_descriptor is None:
                return _error(ErrorCode.NOT_FOUND)
            if not target.enabled:
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            self._check_policy(parent, target_descriptor)
            if parent.origin is InvocationOrigin.WEB_PUBLIC and (
                not self._issuer.allows_public_web(parent.module_id, descriptor)
                or not self._issuer.allows_public_web(
                    target.module_id, target_descriptor
                )
            ):
                raise DependencyCallError("public web dependency is not deployed")
            chain = _ACTIVE_CHAINS.get(
                parent.invocation_id, ((parent.module_id, caller.capability_id),)
            )
            if len(chain) - 1 >= self._max_depth:
                return _error(ErrorCode.UNSUPPORTED)
            target_key = (target.module_id, target_ref.capability_id)
            if target_key in chain:
                return _error(ErrorCode.UNSUPPORTED)
            child = self._issuer.derive(
                parent,
                module_id=target.module_id,
                module_epoch=target.epoch,
                deadline=self._effective_deadline(parent),
                capability_id=target_ref.capability_id,
            )
            child_lease = self._lifecycle.admission.admit(
                child, target_ref.capability_id
            )
            self._lifecycle.guard(
                target.module_id,
                epoch=target.epoch,
                registry_revision=snapshot.revision,
                invocation=child,
            )
            validated = validate_parameters(target_descriptor, parameters)
            handler = target.handlers.capabilities[target_ref.capability_id]
        except (
            DependencyCallError,
            InvalidInvocation,
            LifecycleError,
            ScopeCancelled,
            ScopeDeadlineExceeded,
            ScopeStaleError,
        ):
            self._release_child(child)
            return _error(ErrorCode.MODULE_UNAVAILABLE)
        except ParameterError:
            self._release_child(child)
            return _error(ErrorCode.PARAMETER_ERROR)
        except (KeyError, TypeError, ValueError, RegistryError):
            self._release_child(child)
            return _error(ErrorCode.UNKNOWN)

        if child is None or child_lease is None:
            self._release_child(child)
            return _error(ErrorCode.MODULE_UNAVAILABLE)

        if target_descriptor.privacy_floor is PrivacyFloor.PRIVATE:
            if self._private_authorizer is None:
                self._release_child(child)
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            try:
                private_grant = await self._private_authorizer(parent, child)
                self._require_private_grant(private_grant, child)
                self._lifecycle.admission.check(child_lease)
            except asyncio.CancelledError:
                self._release_child(child)
                raise
            except Exception:
                self._release_child(child)
                return _error(ErrorCode.MODULE_UNAVAILABLE)

        _ACTIVE_CHAINS[child.invocation_id] = chain + (target_key,)
        task = None
        work = None
        try:
            remaining = self._remaining_deadline(child)
            if remaining is not None and remaining <= 0:
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            try:
                scope = self._lifecycle.scope(target.module_id)
                work = handler.invoke(child, validated)
                task = scope.create_task(
                    work,
                    name=f"dependency:{target_ref.capability_id}",
                )
            except (
                LifecycleError,
                ScopeCancelled,
                ScopeDeadlineExceeded,
                ScopeStaleError,
            ):
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            # A synchronous create_task hook or a clock tick can consume the
            # deadline between the pre-admission check and task creation.  Do
            # not let the handler start in that case.
            post_create_remaining = self._remaining_deadline(child)
            if post_create_remaining is not None and post_create_remaining <= 0:
                await self._cancel_and_wait(task, work)
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            try:
                result = await asyncio.wait_for(task, timeout=remaining)
            except asyncio.TimeoutError:
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            except asyncio.CancelledError:
                if task is not None and not task.done():
                    task.cancel()
                raise
            except (
                LifecycleError,
                ScopeCancelled,
                ScopeDeadlineExceeded,
                ScopeStaleError,
            ):
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            except Exception:
                return _error(ErrorCode.UNKNOWN)

            remaining_after = self._remaining_deadline(child)
            if remaining_after is not None and remaining_after <= 0:
                return _error(ErrorCode.MODULE_UNAVAILABLE)

            try:
                self._issuer.require(parent)
                self._issuer.require(child)
                current = self._registry.snapshot()
                current_target = current.module(target.module_id)
                self._lifecycle.guard(
                    target.module_id,
                    epoch=child.module_epoch,
                    registry_revision=child.registry_revision,
                    invocation=child,
                )
                if (
                    current_target.epoch != child.module_epoch
                    or not current_target.enabled
                ):
                    return _error(ErrorCode.MODULE_UNAVAILABLE)
                if (
                    parent.origin is InvocationOrigin.WEB_PUBLIC
                    and not self._issuer.allows_public_web(
                        current_target.module_id,
                        next(
                            (
                                item
                                for item in current_target.manifest.capabilities
                                if item.capability_id == child.capability_id
                            ),
                            None,
                        ),
                    )
                ):
                    return _error(ErrorCode.MODULE_UNAVAILABLE)
                self._lifecycle.admission.check(child_lease)
                parent_lease = self._issuer.lease_for(parent)
                if parent_lease is None:
                    return _error(ErrorCode.MODULE_UNAVAILABLE)
                self._lifecycle.admission.check(parent_lease)
            except (
                InvalidInvocation,
                LifecycleError,
                RegistryError,
                ScopeStaleError,
                ScopeCancelled,
            ):
                return _error(ErrorCode.MODULE_UNAVAILABLE)
            if target_descriptor.privacy_floor is PrivacyFloor.PRIVATE:
                if self._private_authorizer is None:
                    return _error(ErrorCode.MODULE_UNAVAILABLE)
                try:
                    current_grant = await self._private_authorizer(parent, child)
                    self._require_private_grant(current_grant, child)
                    if current_grant != private_grant:
                        return _error(ErrorCode.MODULE_UNAVAILABLE)
                    self._issuer.require(parent)
                    self._issuer.require(child)
                    self._lifecycle.admission.check(parent_lease)
                    self._lifecycle.admission.check(child_lease)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return _error(ErrorCode.MODULE_UNAVAILABLE)
            if not isinstance(result, CapabilityResult):
                return _error(ErrorCode.UNKNOWN)
            expected_privacy = (
                Privacy.PRIVATE
                if target_descriptor.privacy_floor is PrivacyFloor.PRIVATE
                else Privacy.PUBLIC
            )
            from ..core.invocation import Gateway

            try:
                return Gateway._valid_result(result, expected_privacy=expected_privacy)
            except Exception:
                return _error(ErrorCode.UNKNOWN)
        finally:
            if task is not None and not task.done():
                await self._cancel_and_wait(task, work)
            elif task is not None:
                self._close_awaitable(work)
            _ACTIVE_CHAINS.pop(child.invocation_id, None)
            self._release_child(child)

    def _current_source(
        self, invocation: InvocationView
    ) -> tuple[RegistrySnapshot, RegisteredModule]:
        validate_contract(invocation)
        snapshot = self._registry.snapshot()
        source = snapshot.module(invocation.module_id)
        if not source.enabled or source.epoch != invocation.module_epoch:
            raise ScopeStaleError("source invocation is stale")
        return snapshot, source

    @staticmethod
    def _caller_descriptor(
        module: RegisteredModule, caller: CallerCapability
    ) -> CapabilityDescriptor | None:
        validate_contract(caller)
        return next(
            (
                descriptor
                for descriptor in module.manifest.capabilities
                if descriptor.capability_id == caller.capability_id
            ),
            None,
        )

    @staticmethod
    def _target_reference(
        parent: InvocationView, capability: str | CapabilityReference
    ) -> CapabilityReference:
        validate_contract(parent)
        validate_contract(capability)
        if isinstance(capability, CapabilityReference):
            return CapabilityReference_validate(capability)
        if type(capability) is str and capability.strip():
            return validate_contract(CapabilityReference(parent.module_id, capability))
        raise TypeError("capability must be a string or CapabilityReference")

    @staticmethod
    def _dependency_declared(
        caller: CapabilityDescriptor,
        target: CapabilityReference,
        caller_module_id: str,
    ) -> bool:
        validate_contract(caller)
        validate_contract(target)
        for requirement in caller.required_capabilities:
            if isinstance(requirement, CapabilityReference):
                if requirement == target:
                    return True
            elif (
                type(requirement) is str
                and target.module_id == caller_module_id
                and requirement == target.capability_id
            ):
                return True
        return False

    @staticmethod
    def _resolve_target(
        snapshot: RegistrySnapshot, target: CapabilityReference
    ) -> tuple[RegisteredModule | None, CapabilityDescriptor | None]:
        validate_contract(target)
        module = snapshot.modules.get(target.module_id)
        if module is None:
            return None, None
        descriptor = next(
            (
                descriptor
                for descriptor in module.manifest.capabilities
                if descriptor.capability_id == target.capability_id
            ),
            None,
        )
        return module, descriptor

    @staticmethod
    def _check_policy(parent: InvocationView, target: CapabilityDescriptor) -> None:
        validate_contract(parent)
        validate_contract(target)
        if parent.origin is InvocationOrigin.WEB_PUBLIC and (
            not supports_public_read_only(target)
            or InvocationOrigin.WEB_PUBLIC not in target.effective_origins
        ):
            raise DependencyCallError("target is outside the public web surface")
        if target.privacy_floor is PrivacyFloor.OWNER:
            raise DependencyCallError(
                "owner capability cannot be invoked as a dependency"
            )
        if parent.origin in (InvocationOrigin.LLM_TOOL, InvocationOrigin.SCHEDULER):
            if not tool_allowed(target):
                raise DependencyCallError("target is outside the public Tool surface")
        if target.privacy_floor.value == "private" and parent.grant_id is None:
            raise DependencyCallError("private dependency requires the parent grant")

    def _require_private_grant(self, grant: object, view: InvocationView) -> None:
        validate_contract(view)
        if (
            not isinstance(grant, Grant)
            or grant.status is not GrantStatus.ACTIVE
            or grant.grant_id != view.grant_id
            or grant.revision != view.grant_revision
            or grant.module_id != view.module_id
            or view.actor_id is None
            or (grant.expires_at is not None and grant.expires_at <= self._utc_clock())
        ):
            raise ValueError("private authorization is not current")

    def _effective_deadline(self, parent: InvocationView) -> float | None:
        validate_contract(parent)
        if parent.deadline is None:
            return None
        if parent.deadline <= self._clock():
            raise ScopeDeadlineExceeded("parent deadline has expired")
        return parent.deadline

    def _remaining_deadline(self, invocation: InvocationView) -> float | None:
        validate_contract(invocation)
        if invocation.deadline is None:
            return None
        return invocation.deadline - self._clock()

    @staticmethod
    async def _cancel_and_wait(task: asyncio.Task[object], work=None) -> None:
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:
            pass
        DependencyInvoker._close_awaitable(work)

    @staticmethod
    def _close_awaitable(work) -> None:
        close = getattr(work, "close", None)
        if callable(close):
            close()

    def _release_child(self, child: InvocationView | None) -> None:
        validate_contract(child)
        if child is None:
            return
        try:
            # Issuer release triggers Admission and CallerCapability cleanup.
            self._issuer.release(child)
        except InvalidInvocation:
            pass

    @property
    def caller_capability(self) -> CallerCapability:
        return self._caller_capability


ControlledDependencyInvoker = DependencyInvoker

__all__ = ["ControlledDependencyInvoker", "DependencyCallError", "DependencyInvoker"]
