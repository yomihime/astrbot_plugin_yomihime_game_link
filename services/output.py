"""One policy boundary for command, Tool, nested, and subscription results."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Protocol, TypeAlias
from uuid import uuid4

from yomihime_game_link_sdk.contexts import (
    InvocationOrigin,
    InvocationSubscriptionScope,
    InvocationView,
)
from yomihime_game_link_sdk.declarations import CapabilityDescriptor, PrivacyFloor
from yomihime_game_link_sdk.display import (
    DisplayAudience,
    DisplayLimits,
    DisplayRenderer,
    Privacy,
)
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    FactDocument,
    ResultStatus,
)
from yomihime_game_link_sdk.storage import OwnershipKind
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef

from ..core.admission import AdmissionError
from ..core.context_issuer import ContextIssuer
from ..core.contracts.services import (
    CommandOutput,
    GrantStatus,
    SubscriptionOutput,
    ToolOutput,
    TrustedConversationResolver,
)
from ..core.contracts.subscriptions import (
    DeliveryEvent,
    DeliveryState,
    SubscriptionStatus,
)
from ..core.contracts.validation_boundary import validate_contract
from ..core.policy import supports_public_read_only, tool_allowed
from ..core.ports import (
    AdmissionLease,
    AdmissionPort,
    ApprovedSendScheduler,
    DeliveryRepository,
    GrantStore,
    MessagePort,
    MessageReceipt,
    MessageStatus,
    MessageTarget,
    RenderedMessage,
    RootOutputClaim,
    RootOutputConflict,
    RootOutputOutcome,
    RootOutputRepository,
    RootOutputState,
    SendApproval,
    SendPermit,
    SubscriptionStore,
)
from ..core.registry import Registry
from .identity import (
    InvocationPrincipalResolver,
    PrincipalResolutionDenied,
    PrincipalResolutionUnavailable,
)
from .owner_authority import OwnerRouteProofAuthority

OutputRequest: TypeAlias = (
    CommandOutput | CapabilityResult | ToolOutput | SubscriptionOutput
)


class ResourceVisibilityProbe(Protocol):
    """Read-only containment probe over every registered non-public resource."""

    async def contains_non_public_resource_reference(self, text: str) -> bool: ...


class LifecycleApprovedSendScheduler(ApprovedSendScheduler):
    """Synchronously register only approved outbound work in the real module scope."""

    def __init__(self, lifecycle) -> None:
        if lifecycle is None or not callable(getattr(lifecycle, "scope", None)):
            raise TypeError("approved sends require the active Lifecycle controller")
        self._lifecycle = lifecycle
        self._completion_observers = {}
        self._observed_tasks = {}

    def observe_completion(self, name: str, observer) -> None:
        if not isinstance(name, str) or not name.strip() or not callable(observer):
            raise ValueError("completion observer requires a task name and callback")
        if name in self._completion_observers:
            raise RuntimeError("task completion observer name is already active")
        self._completion_observers[name] = observer

    def clear_completion_observer(self, name: str) -> None:
        self._completion_observers.pop(name, None)

    def has_owned_task(self, name: str) -> bool:
        task = self._observed_tasks.get(name)
        return task is not None and not task.done()

    def schedule(self, permit: SendPermit, task, *, name: str) -> None:
        if not isinstance(permit, SendPermit):
            _close_awaitable(task)
            raise TypeError("scheduler requires the exact send permit")
        observer = self._completion_observers.pop(name, None)
        try:
            scope = self._lifecycle.scope(permit.module_id)
            if scope.epoch != permit.module_epoch:
                raise RuntimeError("send permit belongs to an old Lifecycle epoch")
            owned = scope.create_task(task, name=name)
            if observer is not None:
                self._observed_tasks[name] = owned

                def finish(completed) -> None:
                    if self._observed_tasks.get(name) is completed:
                        self._observed_tasks.pop(name, None)
                    observer(completed)

                owned.add_done_callback(finish)
        except BaseException:
            _close_awaitable(task)
            if observer is not None:
                try:
                    observer(None)
                except Exception:
                    pass
            raise


class OutputStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    TOOL_RESULT = "tool_result"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUBSCRIPTION_ENQUEUED = "subscription_enqueued"
    NESTED_RESULT = "nested_result"


@dataclass(frozen=True, slots=True)
class OutputResult:
    """Outcome of routing one structured result through the root outlet."""

    status: OutputStatus
    result: OutputRequest | None = None
    receipt: MessageReceipt | None = None
    error_code: str | None = None


@dataclass(slots=True)
class _InvocationLockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class OutputService:
    """Route trusted root results without allowing modules to send messages.

    A durable root-output claim fences retries of one exact root invocation.
    A new root invocation remains a new output identity.
    """

    def __init__(
        self,
        *,
        issuer: ContextIssuer,
        admission: AdmissionPort,
        send_scheduler: ApprovedSendScheduler,
        registry: Registry,
        renderer: DisplayRenderer,
        limits: DisplayLimits,
        conversations: TrustedConversationResolver,
        message_port: MessagePort,
        deliveries: DeliveryRepository,
        grants: GrantStore,
        subscriptions: SubscriptionStore,
        root_outputs: RootOutputRepository,
        resource_visibility: ResourceVisibilityProbe,
        principal_resolver: InvocationPrincipalResolver,
        owner_authority: OwnerRouteProofAuthority | None = None,
        claim_lease: timedelta,
        send_timeout: float,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        validate_contract(renderer)
        validate_contract(limits)
        self._issuer = issuer
        self._admission = admission
        self._send_scheduler = send_scheduler
        self._registry = registry
        self._renderer = renderer
        self._limits = limits
        self._conversations = conversations
        self._message_port = message_port
        self._deliveries = deliveries
        self._grants = grants
        self._subscriptions = subscriptions
        self._root_outputs = root_outputs
        self._resource_visibility = resource_visibility
        if not callable(getattr(principal_resolver, "principal_id", None)):
            raise TypeError("output service requires a principal resolver")
        self._principal_resolver = principal_resolver
        if owner_authority is not None and not callable(
            getattr(owner_authority, "require_current", None)
        ):
            raise TypeError("output owner authority is invalid")
        self._owner_authority = owner_authority
        if claim_lease.total_seconds() <= 0:
            raise ValueError("root output claim lease must be positive")
        if send_timeout <= 0:
            raise ValueError("message send timeout must be positive")
        self._claim_lease = claim_lease
        self._send_timeout = send_timeout
        self._now = now or (lambda: datetime.now(UTC))
        self._locks: dict[str, _InvocationLockEntry] = {}

    async def progress(self, invocation: InvocationView) -> None:
        """Validate a progress boundary without creating a user-visible send."""

        validate_contract(invocation)
        self._issuer.require(invocation)

    async def route(
        self, invocation: InvocationView, request: OutputRequest
    ) -> OutputResult:
        """Validate the exact issued view, then route by its trusted origin."""

        validate_contract(invocation)
        try:
            trusted = self._issuer.require(invocation)
            self._require_current_module(trusted)
            # A derived result is data for its already admitted parent and has
            # no independent authority to create a host send.
            if trusted.parent_id is None:
                lease = self._issuer.lease_for(trusted)
                if not isinstance(lease, AdmissionLease):
                    raise AdmissionError(
                        "root output requires its exact admission lease"
                    )
                self._admission.check(lease)
        except Exception:
            return _failure("invocation_unavailable")

        # A nested capability contributes data to its parent. It never owns a
        # host conversation send or a subscription scheduling action.
        if trusted.parent_id is not None:
            return OutputResult(OutputStatus.NESTED_RESULT, result=request)

        if isinstance(request, CommandOutput):
            if trusted.origin is not InvocationOrigin.COMMAND:
                return _failure("origin_mismatch")
            return await self._command(trusted, request)
        if isinstance(request, ToolOutput):
            if trusted.origin is not InvocationOrigin.LLM_TOOL:
                return _failure("origin_mismatch")
            return _failure("tool_provenance_required")
        if isinstance(request, CapabilityResult):
            if trusted.origin is InvocationOrigin.COMMAND:
                return await self._command(trusted, CommandOutput(request))
            if trusted.origin is InvocationOrigin.LLM_TOOL:
                return await self._tool_result(trusted, request)
            return _failure("origin_mismatch")
        if isinstance(request, SubscriptionOutput):
            if trusted.origin is not InvocationOrigin.SUBSCRIPTION:
                return _failure("origin_mismatch")
            return await self._subscription(trusted, request)
        return _failure("unsupported_output")

    def _require_current_module(
        self, invocation: InvocationView
    ) -> CapabilityDescriptor | None:
        validate_contract(invocation)
        snapshot = self._registry.snapshot()
        module = snapshot.module(invocation.module_id)
        if not module.enabled or module.epoch != invocation.module_epoch:
            raise ValueError("invocation module is no longer current")
        if invocation.capability_id is None:
            return None
        capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == invocation.capability_id
            ),
            None,
        )
        if capability is None:
            raise ValueError("invocation capability is no longer declared")
        return capability

    def _root_lease(self, invocation: InvocationView) -> AdmissionLease:
        validate_contract(invocation)
        lease = self._issuer.lease_for(invocation)
        if not isinstance(lease, AdmissionLease):
            raise AdmissionError("root output has no exact AdmissionLease")
        return lease

    @asynccontextmanager
    async def _serialized_invocation(self, invocation_id: str) -> AsyncIterator[None]:
        entry = self._locks.get(invocation_id)
        if entry is None:
            entry = _InvocationLockEntry()
            self._locks[invocation_id] = entry
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0 and self._locks.get(invocation_id) is entry:
                del self._locks[invocation_id]

    async def _command(
        self, invocation: InvocationView, request: CommandOutput
    ) -> OutputResult:
        validate_contract(invocation)
        async with self._serialized_invocation(invocation.invocation_id):
            result = request.result
            try:
                capability = self._require_current_module(invocation)
                if capability is None:
                    raise _OutputFailure("invocation_unavailable")
                await self._check_result_authority(invocation, capability, result)
                target = await self._target(invocation, result.privacy)
                rendered = await self._render(result)
                self._admission.check(self._root_lease(invocation))
            except _OutputFailure as exc:
                return await self._controlled(
                    invocation,
                    result.result_id,
                    {"request": request, "code": exc.code},
                    exc.code,
                )
            except Exception:
                return await self._controlled(
                    invocation,
                    result.result_id,
                    {"request": request, "code": "output_unavailable"},
                    "output_unavailable",
                )

            fingerprint = _fingerprint(
                {
                    "result_id": result.result_id,
                    "route": target,
                    "payload": rendered,
                    "privacy": result.privacy,
                    "module_id": invocation.module_id,
                    "capability_id": invocation.capability_id,
                }
            )
            try:
                claim = await self._claim(invocation, result.result_id, fingerprint)
            except _OutputInProgress:
                return OutputResult(OutputStatus.IN_PROGRESS, result=request)
            except _OutputFailure as exc:
                return _failure(exc.code)
            replay = self._replay(claim, request)
            if replay is not None:
                return replay
            if claim.state is not RootOutputState.CLAIMED:
                return OutputResult(OutputStatus.IN_PROGRESS, result=request)

            lease = self._root_lease(invocation)
            completed: asyncio.Future[OutputResult] = (
                asyncio.get_running_loop().create_future()
            )
            state: dict[str, object] = {}

            async def abort_before_dispatch(
                approval: SendApproval, error_code: str
            ) -> None:
                sending = state.get("sending")
                if (
                    not isinstance(sending, RootOutputClaim)
                    or approval.claim_id != sending.owner_token
                ):
                    return
                await self._root_outputs.abort_before_dispatch(
                    sending,
                    completed_at=self._utc_now(),
                    error_code=_stable_error_code(error_code),
                )

            async def final_check() -> SendApproval | None:
                try:
                    grant_expiry = await self._validate_command_current(
                        invocation, result, target
                    )
                except _OutputFailure as exc:
                    state["failure"] = exc.code
                    state["result"] = await self._complete_controlled_claim(
                        claim, exc.code
                    )
                    return None
                except asyncio.CancelledError:
                    await _drain_cleanup(
                        self._reconcile_cancelled_root_claim(
                            claim, invocation, result.result_id, fingerprint
                        )
                    )
                    raise
                except Exception:
                    state["failure"] = "output_unavailable"
                    state["result"] = await self._complete_controlled_claim(
                        claim, "output_unavailable"
                    )
                    return None

                now = self._utc_now()
                try:
                    sending = await self._root_outputs.begin_sending(
                        claim,
                        now=now,
                        lease_expires_at=now + self._claim_lease,
                    )
                except asyncio.CancelledError:
                    await _drain_cleanup(
                        self._reconcile_cancelled_root_claim(
                            claim, invocation, result.result_id, fingerprint
                        )
                    )
                    raise
                except Exception:
                    await _drain_cleanup(
                        self._reconcile_cancelled_root_claim(
                            claim, invocation, result.result_id, fingerprint
                        )
                    )
                    state["failure"] = "output_claim_unavailable"
                    return None
                if sending is None:
                    state["result"] = OutputResult(
                        OutputStatus.IN_PROGRESS, result=request
                    )
                    return None

                approval = SendApproval(sending.owner_token, ())
                state["sending"] = sending
                # Recheck owner identity after the durable SENDING transition;
                # no await follows the final owner proof check on success.
                try:
                    if capability.privacy_floor is PrivacyFloor.OWNER:
                        if self._owner_authority is None:
                            raise _OutputFailure("owner_proof_unavailable")
                        await self._owner_authority.require_current(invocation)
                    self._issuer.require(invocation)
                    self._require_current_module(invocation)
                    self._admission.check(lease)
                    if sending.lease_expires_at <= self._utc_now():
                        raise _OutputFailure("invocation_unavailable")
                    if grant_expiry is not None and grant_expiry <= self._utc_now():
                        raise _OutputFailure("grant_unavailable")
                except BaseException as exc:
                    code = (
                        exc.code
                        if isinstance(exc, _OutputFailure)
                        else "invocation_unavailable"
                    )
                    await _drain_cleanup(abort_before_dispatch(approval, code))
                    state["failure"] = code
                    return None
                return approval

            async def send(permit: SendPermit) -> object:
                if permit.lease_id != lease.lease_id:
                    raise RuntimeError("send permit does not belong to root invocation")
                sending = state.get("sending")
                if not isinstance(sending, RootOutputClaim):
                    raise RuntimeError("root send has no persisted SENDING claim")
                try:
                    receipt = await self._message_port.send(target, rendered)
                    if not isinstance(receipt, MessageReceipt):
                        receipt = MessageReceipt(MessageStatus.UNKNOWN)
                except asyncio.CancelledError:
                    await _drain_cleanup(
                        self._persist_root_receipt(
                            sending, MessageReceipt(MessageStatus.UNKNOWN)
                        )
                    )
                    if not completed.done():
                        completed.set_result(
                            OutputResult(
                                OutputStatus.UNKNOWN,
                                result=request,
                                error_code="host_send_cancelled",
                            )
                        )
                    raise
                except Exception:
                    receipt = MessageReceipt(MessageStatus.UNKNOWN)
                    final = await self._persist_root_receipt(sending, receipt)
                    if not completed.done():
                        completed.set_result(
                            OutputResult(
                                OutputStatus.UNKNOWN,
                                result=request,
                                error_code=(
                                    None
                                    if final is not None
                                    else "storage_finish_failed"
                                ),
                            )
                        )
                    return receipt
                final = await self._persist_root_receipt(sending, receipt)
                if not completed.done():
                    completed.set_result(
                        self._root_result(request, receipt, final is not None)
                    )
                return receipt

            try:
                permit = await self._admission.approve_and_schedule_send(
                    lease,
                    name=f"root-output:{invocation.module_id}",
                    final_check=final_check,
                    sender=send,
                    scheduler=self._send_scheduler,
                    abort_before_dispatch=abort_before_dispatch,
                )
            except asyncio.CancelledError:
                raise
            except AdmissionError:
                return await self._complete_controlled_claim(
                    claim, "invocation_unavailable"
                )
            except Exception:
                failure = state.get("failure")
                return _failure(
                    failure if isinstance(failure, str) else "output_unavailable"
                )
            if permit is None:
                value = state.get("result")
                if isinstance(value, OutputResult):
                    return value
                failure = state.get("failure")
                if isinstance(failure, str):
                    return _failure(failure)
                return OutputResult(OutputStatus.IN_PROGRESS, result=request)
            try:
                return await asyncio.wait_for(
                    asyncio.shield(completed), timeout=self._send_timeout
                )
            except TimeoutError:
                return OutputResult(
                    OutputStatus.UNKNOWN,
                    result=request,
                    error_code="host_send_timeout",
                )

    async def _validate_command_current(
        self,
        invocation: InvocationView,
        result: CapabilityResult,
        expected_target: MessageTarget,
    ) -> datetime | None:
        validate_contract(invocation)
        validate_contract(result)
        try:
            self._issuer.require(invocation)
            capability = self._require_current_module(invocation)
        except Exception:
            raise _OutputFailure("invocation_unavailable") from None
        if capability is None:
            raise _OutputFailure("invocation_unavailable")
        grant_expiry = await self._check_result_authority(
            invocation, capability, result
        )
        current_target = await self._target(invocation, result.privacy)
        if current_target != expected_target:
            raise _OutputFailure("route_changed")
        if capability.privacy_floor is PrivacyFloor.OWNER:
            if self._owner_authority is None:
                raise _OutputFailure("owner_proof_unavailable")
            try:
                await self._owner_authority.require_current(invocation)
            except Exception:
                raise _OutputFailure("owner_proof_unavailable") from None
        # Keep the issuer and registry check as the final synchronous authority
        # read after awaited Grant and route lookups.
        try:
            self._issuer.require(invocation)
            self._require_current_module(invocation)
        except Exception:
            raise _OutputFailure("invocation_unavailable") from None
        return grant_expiry

    async def _reconcile_cancelled_root_claim(
        self,
        original: RootOutputClaim,
        invocation: InvocationView,
        output_identity: str,
        fingerprint: str,
    ) -> None:
        """Resolve an interrupted begin_sending by reading the exact owner row."""
        validate_contract(invocation)
        now = self._utc_now()
        try:
            current = await self._root_outputs.claim(
                invocation.invocation_id,
                output_identity,
                fingerprint,
                original.owner_token,
                now=now,
                lease_expires_at=now + self._claim_lease,
            )
        except Exception:
            return
        if (
            current.claim_generation != original.claim_generation
            or current.owner_token != original.owner_token
        ):
            return
        if current.state is RootOutputState.SENDING:
            await self._root_outputs.abort_before_dispatch(
                current,
                completed_at=self._utc_now(),
                error_code="cancelled_before_dispatch",
            )
        elif current.state is RootOutputState.CLAIMED:
            await self._root_outputs.complete(
                current,
                None,
                outcome=RootOutputOutcome.CONTROLLED_RESULT,
                completed_at=self._utc_now(),
                error_code="cancelled_before_dispatch",
            )

    async def _persist_root_receipt(
        self, sending: RootOutputClaim, receipt: MessageReceipt
    ) -> RootOutputClaim | None:
        try:
            return await self._root_outputs.complete(
                sending,
                receipt,
                outcome=RootOutputOutcome.MESSAGE,
                completed_at=self._utc_now(),
            )
        except Exception:
            return None

    @staticmethod
    def _root_result(
        request: CommandOutput, receipt: MessageReceipt, persisted: bool
    ) -> OutputResult:
        status = {
            MessageStatus.ACCEPTED: OutputStatus.SENT,
            MessageStatus.FAILED: OutputStatus.FAILED,
            MessageStatus.UNKNOWN: OutputStatus.UNKNOWN,
        }[receipt.status]
        return OutputResult(
            status,
            result=request,
            receipt=receipt,
            error_code=None if persisted else "storage_finish_failed",
        )

    async def _check_result_authority(
        self,
        invocation: InvocationView,
        capability: CapabilityDescriptor,
        result: CapabilityResult,
    ) -> datetime | None:
        validate_contract(invocation)
        validate_contract(capability)
        validate_contract(result)
        if not isinstance(result, CapabilityResult):
            raise _OutputFailure("invalid_result")
        owner_floor = capability.privacy_floor is PrivacyFloor.OWNER
        if owner_floor:
            if (
                self._owner_authority is None
                or invocation.grant_id is not None
                or invocation.grant_revision is not None
                or result.privacy is not Privacy.PRIVATE
            ):
                raise _OutputFailure("owner_proof_unavailable")
            try:
                await self._owner_authority.require_current(invocation)
            except Exception:
                raise _OutputFailure("owner_proof_unavailable") from None
        elif result.privacy is Privacy.PRIVATE and invocation.grant_id is None:
            raise _OutputFailure("grant_required")
        if (
            capability.privacy_floor is PrivacyFloor.PRIVATE
            and invocation.grant_id is None
        ):
            raise _OutputFailure("grant_required")
        if invocation.grant_id is not None:
            if invocation.grant_revision is None or invocation.actor_id is None:
                raise _OutputFailure("grant_unavailable")
            try:
                self._admission.check(self._root_lease(invocation))
                principal_id = await self._principal_resolver.principal_id(invocation)
                self._admission.check(self._root_lease(invocation))
            except (PrincipalResolutionDenied, PrincipalResolutionUnavailable):
                raise _OutputFailure("grant_unavailable") from None
            except Exception:
                raise _OutputFailure("grant_unavailable") from None
            grant = await self._grants.current_grant(invocation.grant_id)
            now = self._now()
            if (
                grant is None
                or grant.status is not GrantStatus.ACTIVE
                or grant.grant_id != invocation.grant_id
                or grant.revision != invocation.grant_revision
                or grant.principal_id != principal_id
                or grant.module_id != invocation.module_id
                or (grant.expires_at is not None and grant.expires_at <= now)
            ):
                raise _OutputFailure("grant_unavailable")
            return grant.expires_at
        return None

    async def _target(
        self, invocation: InvocationView, privacy: Privacy
    ) -> MessageTarget:
        validate_contract(invocation)
        validate_contract(privacy)
        if invocation.adapter_id is None or invocation.conversation_id is None:
            raise _OutputFailure("route_unavailable")
        try:
            route = await self._conversations.resolve(invocation)
        except Exception:
            route = None
        if (
            not isinstance(route, ConversationRef)
            or route.adapter_id != invocation.adapter_id
            or route.conversation_id != invocation.conversation_id
        ):
            raise _OutputFailure("route_unavailable")
        authorized = privacy is Privacy.PRIVATE or invocation.grant_id is not None
        if authorized and route.kind is not ConversationKind.DIRECT:
            raise _OutputFailure("private_recipient_required")
        return MessageTarget(
            conversation_id=route.conversation_id,
            recipient_id=invocation.actor_id
            if route.kind is ConversationKind.DIRECT
            else None,
            conversation=route,
            authorized=authorized,
        )

    async def _render(self, result: CapabilityResult) -> RenderedMessage:
        validate_contract(result)
        if result.status is ResultStatus.ERROR:
            # Use stable core error text, never module or exception strings.
            message = _ERROR_MESSAGES.get(result.error.code, "request failed")
            return RenderedMessage(message)
        if result.document is None:
            raise _OutputFailure("invalid_result")
        audience = (
            DisplayAudience.PRIVATE
            if result.privacy is Privacy.PRIVATE
            else DisplayAudience.PUBLIC
        )
        try:
            output = await self._renderer.render(
                result.document, limits=self._limits, audience=audience
            )
            validate_contract(output)
        except Exception:
            raise _OutputFailure("display_render_failed") from None
        from yomihime_game_link_sdk.display import DisplayOutput

        if type(output) is not DisplayOutput:
            raise _OutputFailure("display_render_failed")
        return RenderedMessage(output.text, output.resource_ids)

    async def _tool_result(
        self, invocation: InvocationView, source: CapabilityResult
    ) -> OutputResult:
        validate_contract(invocation)
        validate_contract(source)
        try:
            self._issuer.require(invocation)
            request = await self._project_public_tool(invocation, source)
        except _OutputFailure as exc:
            return await self._controlled(
                invocation,
                source.result_id,
                {"source": source, "route": "tool"},
                exc.code,
            )
        except Exception:
            return _failure("invocation_unavailable")
        if request is None:
            return await self._controlled(
                invocation,
                source.result_id,
                {"source": source, "route": "tool"},
                "tool_result_not_public",
            )
        try:
            claim = await self._claim(
                invocation,
                source.result_id,
                _fingerprint(
                    {
                        "source": source,
                        "facts": request,
                        "module_id": invocation.module_id,
                        "capability_id": invocation.capability_id,
                    }
                ),
            )
        except _OutputFailure as exc:
            return _failure(exc.code)
        except _OutputInProgress:
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)
        try:
            request = await self._project_public_tool(invocation, source)
        except _OutputFailure as exc:
            if claim.state is RootOutputState.CLAIMED:
                return await self._complete_controlled_claim(claim, exc.code)
            return _failure(exc.code)
        except Exception:
            if claim.state is RootOutputState.CLAIMED:
                return await self._complete_controlled_claim(
                    claim, "tool_result_not_public"
                )
            return _failure("tool_result_not_public")
        if request is None:
            if claim.state is RootOutputState.CLAIMED:
                return await self._complete_controlled_claim(
                    claim, "tool_result_not_public"
                )
            return _failure("tool_result_not_public")
        replay = self._replay(claim, request)
        if replay is not None:
            return replay
        if claim.state is not RootOutputState.CLAIMED:
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)
        try:
            completed = await self._root_outputs.complete(
                claim,
                None,
                outcome=RootOutputOutcome.TOOL_RETURNED,
                completed_at=self._utc_now(),
            )
        except Exception:
            return _failure("output_finish_unavailable")
        if completed is None:
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)
        # Completion itself is awaited. An issuer or policy change during that
        # wait must suppress the facts even though the idempotency outcome is
        # already terminal; do not issue a new claim or expose stale facts.
        try:
            final_request = await self._project_public_tool(invocation, source)
        except _OutputFailure as exc:
            return _failure(exc.code)
        except Exception:
            return _failure("tool_result_not_public")
        if final_request is None:
            return _failure("tool_result_not_public")
        return OutputResult(OutputStatus.TOOL_RESULT, result=final_request)

    async def _project_public_tool(
        self, invocation: InvocationView, source: CapabilityResult
    ) -> ToolOutput | None:
        validate_contract(invocation)
        validate_contract(source)
        try:
            self._issuer.require(invocation)
            capability = self._require_current_module(invocation)
        except Exception:
            raise _OutputFailure("invocation_unavailable") from None
        if capability is None:
            raise _OutputFailure("invocation_unavailable")
        snapshot = self._registry.snapshot()
        module = snapshot.module(invocation.module_id)
        declared_tool = any(
            item.capability_id == invocation.capability_id
            for item in module.manifest.tools
        )
        if (
            not declared_tool
            or capability.privacy_floor is not PrivacyFloor.PUBLIC
            or not supports_public_read_only(capability)
            or not tool_allowed(capability)
            or invocation.grant_id is not None
            or not isinstance(source, CapabilityResult)
            or source.privacy is not Privacy.PUBLIC
            or (
                source.document is not None
                and source.document.privacy is not Privacy.PUBLIC
            )
            or not isinstance(source.model_facts, FactDocument)
        ):
            return None
        try:
            await _require_public_fact_resources(
                source.model_facts, self._resource_visibility
            )
        except _OutputFailure:
            raise
        except Exception:
            raise _OutputFailure("tool_resource_visibility_unavailable") from None
        # Resource lookups are awaited. Revalidate issuer and Registry afterwards
        # so a module disabled during a probe cannot return facts.
        try:
            self._issuer.require(invocation)
            latest = self._require_current_module(invocation)
        except Exception:
            raise _OutputFailure("invocation_unavailable") from None
        if latest is None or latest != capability:
            raise _OutputFailure("invocation_unavailable")
        return ToolOutput(source.model_facts)

    async def _subscription(
        self, invocation: InvocationView, request: SubscriptionOutput
    ) -> OutputResult:
        validate_contract(invocation)
        event = request.event
        try:
            record = await self._subscription_record(invocation, event)
            fingerprint = _fingerprint(
                {
                    "event": event,
                    "record": record,
                    "scope": invocation.subscription_scope,
                    "route": (
                        invocation.adapter_id,
                        invocation.conversation_kind,
                        invocation.conversation_id,
                        invocation.delivery_route,
                    ),
                }
            )
            claim = await self._claim(invocation, event.idempotency_key, fingerprint)
        except _OutputFailure as exc:
            return _failure(exc.code)
        except _OutputInProgress:
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)
        except Exception:
            return _failure("subscription_unavailable")

        replay = self._replay(claim, request)
        if replay is not None:
            return replay
        if claim.state is not RootOutputState.CLAIMED:
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)

        # Re-read mutable authority after the root claim and immediately before
        # enqueue. Delivery independently repeats authority checks before send.
        try:
            current_record = await self._subscription_record(invocation, event)
        except _OutputFailure as exc:
            return await self._complete_controlled_claim(claim, exc.code)
        except Exception:
            return await self._complete_controlled_claim(
                claim, "subscription_unavailable"
            )
        if current_record != record:
            return await self._complete_controlled_claim(
                claim, "subscription_revision_changed"
            )

        try:
            existing = await self._deliveries.current_event(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
            )
            if existing is not None:
                if not _same_delivery_payload(existing, event):
                    return await self._complete_controlled_claim(
                        claim, "delivery_event_conflict"
                    )
            else:
                persisted = await self._deliveries.create_event(event)
                if not _same_delivery_payload(persisted, event):
                    return await self._complete_controlled_claim(
                        claim, "delivery_event_conflict"
                    )
        except Exception:
            # create_event may have committed before a connection failure. Keep
            # CLAIMED so a retry can inspect the exact persisted event safely.
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)

        try:
            completed = await self._root_outputs.complete(
                claim,
                None,
                outcome=RootOutputOutcome.SUBSCRIPTION_ENQUEUED,
                completed_at=self._utc_now(),
            )
        except Exception:
            # The event itself is durable; replay will verify it and complete
            # the receipt-free output record without creating a second event.
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)
        if completed is None:
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)
        return OutputResult(OutputStatus.SUBSCRIPTION_ENQUEUED, result=request)

    async def _subscription_record(
        self, invocation: InvocationView, event: DeliveryEvent
    ):
        validate_contract(invocation)
        if (
            invocation.parent_id is not None
            or invocation.origin is not InvocationOrigin.SUBSCRIPTION
            or invocation.actor_id is None
            or invocation.subscription_id != event.subscription_id
            or invocation.subscription_revision != event.subscription_revision
            or invocation.subscription_scope is None
            or invocation.conversation_kind is None
            or invocation.adapter_id is None
            or invocation.conversation_id is None
            or invocation.delivery_route is None
        ):
            raise _OutputFailure("subscription_invocation_invalid")
        self._issuer.require(invocation)
        self._require_current_module(invocation)
        record = await self._subscriptions.current(event.subscription_id)
        if (
            record is None
            or record.status is not SubscriptionStatus.ACTIVE
            or record.subscription_id != invocation.subscription_id
            or record.revision != invocation.subscription_revision
            or record.owner_id != invocation.actor_id
            or record.module_id != invocation.module_id
            or record.owner_id != event.owner_id
            or record.grant != event.grant
            or record.recipient != event.recipient
            or event.state is not DeliveryState.PENDING
        ):
            raise _OutputFailure("subscription_revision_changed")

        recipient = event.recipient
        if (
            recipient.adapter_id != invocation.adapter_id
            or recipient.kind.value != invocation.conversation_kind.value
            or recipient.conversation_id != invocation.conversation_id
            or recipient.delivery_route != invocation.delivery_route
        ):
            raise _OutputFailure("subscription_route_mismatch")

        scope_kind = record.collection_key.scope.kind
        expected_scope = {
            OwnershipKind.PUBLIC: InvocationSubscriptionScope.PUBLIC,
            OwnershipKind.USER: InvocationSubscriptionScope.USER,
            OwnershipKind.AUTHORIZED: InvocationSubscriptionScope.AUTHORIZED,
        }[scope_kind]
        if expected_scope is not invocation.subscription_scope:
            raise _OutputFailure("subscription_scope_mismatch")
        if scope_kind is OwnershipKind.AUTHORIZED:
            if (
                record.grant is None
                or record.grant != event.grant
                or recipient.kind is not ConversationKind.DIRECT
                or event.display_data.privacy is not Privacy.PRIVATE
                or invocation.grant_id != record.grant.grant_id
                or invocation.grant_revision != record.grant.revision
            ):
                raise _OutputFailure("grant_unavailable")
        elif invocation.grant_id is not None or event.grant is not None:
            raise _OutputFailure("subscription_scope_mismatch")
        if (
            scope_kind is OwnershipKind.PUBLIC
            and event.display_data.privacy is Privacy.PRIVATE
        ):
            raise _OutputFailure("privacy_rejected")
        if record.grant is not None:
            grant = await self._grants.current_grant(record.grant.grant_id)
            now = self._utc_now()
            if (
                grant is None
                or grant.status is not GrantStatus.ACTIVE
                or grant.revision != record.grant.revision
                or grant.principal_id != record.owner_id
                or grant.module_id != record.module_id
                or (grant.expires_at is not None and grant.expires_at <= now)
            ):
                raise _OutputFailure("grant_unavailable")
        return record

    async def _claim(
        self, invocation: InvocationView, output_identity: str, fingerprint: str
    ) -> RootOutputClaim:
        validate_contract(invocation)
        now = self._utc_now()
        owner_token = uuid4().hex
        try:
            claim = await self._root_outputs.claim(
                invocation.invocation_id,
                output_identity,
                fingerprint,
                owner_token,
                now=now,
                lease_expires_at=now + self._claim_lease,
            )
        except RootOutputConflict:
            raise _OutputFailure("root_output_conflict") from None
        except Exception:
            raise _OutputFailure("output_claim_unavailable") from None
        if (
            claim.state in (RootOutputState.CLAIMED, RootOutputState.SENDING)
            and claim.owner_token != owner_token
        ):
            raise _OutputInProgress()
        return claim

    def _replay(
        self, claim: RootOutputClaim, request: OutputRequest | None
    ) -> OutputResult | None:
        if claim.state is RootOutputState.UNKNOWN:
            return OutputResult(
                OutputStatus.UNKNOWN, result=request, error_code=claim.error_code
            )
        if claim.state is RootOutputState.CLAIMED:
            return None
        if claim.state is RootOutputState.SENDING:
            return OutputResult(OutputStatus.IN_PROGRESS, result=request)
        if claim.state is not RootOutputState.COMPLETED:
            return _failure("output_state_invalid")
        if claim.outcome is RootOutputOutcome.MESSAGE:
            assert claim.receipt is not None
            status = {
                MessageStatus.ACCEPTED: OutputStatus.SENT,
                MessageStatus.FAILED: OutputStatus.FAILED,
                MessageStatus.UNKNOWN: OutputStatus.UNKNOWN,
            }[claim.receipt.status]
            return OutputResult(status, result=request, receipt=claim.receipt)
        if claim.outcome is RootOutputOutcome.TOOL_RETURNED:
            return OutputResult(OutputStatus.TOOL_RESULT, result=request)
        if claim.outcome is RootOutputOutcome.SUBSCRIPTION_ENQUEUED:
            return OutputResult(OutputStatus.SUBSCRIPTION_ENQUEUED, result=request)
        return _failure(claim.error_code or "output_failed")

    async def _controlled(
        self,
        invocation: InvocationView,
        output_identity: str,
        payload: object,
        code: str,
    ) -> OutputResult:
        validate_contract(invocation)
        try:
            claim = await self._claim(
                invocation, output_identity, _fingerprint(payload)
            )
        except _OutputInProgress:
            return OutputResult(OutputStatus.IN_PROGRESS)
        except _OutputFailure as exc:
            return _failure(exc.code)
        replay = self._replay(claim, None)
        if replay is not None:
            return replay
        return await self._complete_controlled_claim(claim, code)

    async def _complete_controlled_claim(
        self, claim: RootOutputClaim, code: str
    ) -> OutputResult:
        if claim.state is not RootOutputState.CLAIMED:
            return OutputResult(OutputStatus.PENDING, error_code=code)
        try:
            completed = await self._root_outputs.complete(
                claim,
                None,
                outcome=RootOutputOutcome.CONTROLLED_RESULT,
                completed_at=self._utc_now(),
                error_code=code,
            )
        except Exception:
            return _failure("output_finish_unavailable")
        if completed is None:
            return OutputResult(OutputStatus.PENDING, error_code=code)
        return _failure(code)

    def _utc_now(self) -> datetime:
        value = self._now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
            or value.utcoffset().total_seconds() != 0
        ):
            raise ValueError("output clock must return an aware UTC instant")
        return value.astimezone(UTC)


class _OutputFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class _OutputInProgress(Exception):
    """Another caller owns the still-live claim for this root invocation."""


def _failure(code: str) -> OutputResult:
    return OutputResult(OutputStatus.FAILED, error_code=code)


def _close_awaitable(work: object) -> None:
    close = getattr(work, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


async def _drain_cleanup(work) -> object | None:
    """Keep an exact compensation running through repeated caller cancellation."""
    try:
        task = asyncio.create_task(work)
    except BaseException:
        _close_awaitable(work)
        return None
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                try:
                    return task.result()
                except BaseException:
                    return None
        except Exception:
            return None


def _stable_error_code(value: str) -> str:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 120
        and all(character.isalnum() or character in "_-" for character in value)
    ):
        return value
    return "dispatch_failed"


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("output payload contains a non-finite number")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("output payload contains a non-finite decimal")
        return {"decimal": str(value)}
    if isinstance(value, (datetime, date)):
        return {"date": value.isoformat()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("output mapping keys must be text")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in fields(value)
        }
    raise TypeError("output payload contains an unsupported value")


def _same_delivery_payload(saved: DeliveryEvent, expected: DeliveryEvent) -> bool:
    """Compare immutable event identity/content while allowing D state progress."""

    if not isinstance(saved, DeliveryEvent) or not isinstance(expected, DeliveryEvent):
        return False
    normalized = replace(
        saved,
        state=expected.state,
        attempt=expected.attempt,
        retry_at=expected.retry_at,
    )
    return normalized == expected


async def _require_public_fact_resources(
    facts: FactDocument, probe: ResourceVisibilityProbe
) -> None:
    """Reject resource references registered at any non-public scope.

    The bounded walk includes mapping keys, values, nested collections, and
    source strings. The probe must detect containment across all non-public
    registered IDs and scopes.
    """

    validate_contract(facts)
    from ..core.public_result import public_fact_strings

    try:
        for value in public_fact_strings(facts):
            try:
                is_private = await probe.contains_non_public_resource_reference(value)
            except Exception:
                raise _OutputFailure("tool_resource_visibility_unavailable") from None
            if not isinstance(is_private, bool):
                raise _OutputFailure("tool_resource_visibility_unavailable")
            if is_private:
                raise _OutputFailure("tool_result_not_public")
    except ValueError:
        raise _OutputFailure("tool_facts_exceed_limits") from None


_ERROR_MESSAGES = {
    ErrorCode.PARAMETER_ERROR: "invalid parameters",
    ErrorCode.UNBOUND: "account binding required",
    ErrorCode.AUTH_REQUIRED: "此来源需要授权。请联系管理员配置对应来源的凭据后重试；请勿在聊天中发送凭据。",
    ErrorCode.AUTH_EXPIRED: "此来源授权已失效。请联系管理员更新对应来源的凭据后重试；请勿在聊天中发送凭据。",
    ErrorCode.NOT_FOUND: "not found",
    ErrorCode.NOT_PUBLIC: "result is not public",
    ErrorCode.NO_RECORDS: "no records found",
    ErrorCode.UNPARSED: "result could not be interpreted",
    ErrorCode.RATE_LIMITED: "request rate limited",
    ErrorCode.UPSTREAM_ERROR: "source temporarily unavailable",
    ErrorCode.MODULE_UNAVAILABLE: "module temporarily unavailable",
    ErrorCode.UNSUPPORTED: "operation unsupported",
    ErrorCode.UNKNOWN: "request failed",
}


__all__ = [
    "LifecycleApprovedSendScheduler",
    "OutputResult",
    "OutputService",
    "OutputStatus",
    "ResourceVisibilityProbe",
]
