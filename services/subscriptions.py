"""Command subscription management and subscription-scoped matching."""

from __future__ import annotations

import inspect
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Protocol

from ..api.contexts import InvocationOrigin, InvocationView
from ..api.display import DigestMember
from ..api.manifests import InvocationPolicy, ModuleManifest, PrivacyFloor
from ..api.services import (
    Grant,
    GrantStatus,
    SubscriptionOperations,
    SubscriptionUnavailable,
    SubscriptionView,
)
from ..api.storage import GrantReference, OwnerScope, OwnershipKind
from ..api.subscriptions import (
    CollectionKey,
    ConversationKind,
    ConversationRef,
    DeliveryEvent,
    DeliveryState,
    DigestMemberAssociation,
    DigestWindowSelector,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ObservationCursor,
    ObservationEvaluationCommit,
    ScheduleDescriptor,
    SubscriptionEvaluationCommit,
    SubscriptionJobAssociation,
    SubscriptionJobChange,
    SubscriptionJobChangeKind,
    SubscriptionRecord,
    SubscriptionRequest,
    SubscriptionStatus,
    delivery_idempotency_key,
    validate_evaluation_decision,
)
from ..core.context_issuer import ContextIssuer
from ..core.ports import (
    AdmissionLease,
    AdmissionPort,
    CollectionRunRequest,
    DigestWindowRepository,
    ExecutionLease,
    GrantStore,
    RevisionConflict,
    SchedulerRepository,
    SubscriptionJobRepository,
    SubscriptionLifecycleRepository,
    SubscriptionStore,
)
from ..core.registry import RegisteredModule, Registry
from .identity import (
    InvocationPrincipalResolver,
    PrincipalResolutionDenied,
    PrincipalResolutionUnavailable,
)
from .owner_authority import OwnerRouteProofAuthority


class SubscriptionOperationError(PermissionError):
    """Sanitized failure to authorize or construct a subscription operation."""

    code = "subscription_operation_unavailable"

    def __init__(self, message: str = "subscription operation is unavailable"):
        super().__init__(message)


class PrivateRecipientRequired(SubscriptionOperationError):
    code = "private_recipient_required"

    def __init__(self) -> None:
        super().__init__("private_recipient_required")


class DigestWindowUnavailable(SubscriptionOperationError):
    code = "digest_window_unavailable"

    def __init__(self) -> None:
        super().__init__("digest window is unavailable")


class TrustedConversationResolver(Protocol):
    async def resolve(self, invocation: InvocationView) -> ConversationRef | None: ...


class CadencePolicy(Protocol):
    """Return deployment-selected cadence seconds and its config revision."""

    def resolve(
        self, module_id: str, schedule: ScheduleDescriptor
    ) -> tuple[float, int]: ...


def _schema_matches(
    schema: Mapping[str, object], value: object, depth: int = 0
) -> bool:
    if depth > 64:
        return False
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
            return False
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        if not isinstance(properties, Mapping) or not isinstance(required, tuple):
            return False
        if set(value) - set(properties) or set(required) - set(value):
            return False
        return all(
            _schema_matches(properties[key], item, depth + 1)
            for key, item in value.items()
        )
    if kind == "array":
        return isinstance(value, (tuple, list)) and all(
            _schema_matches(schema["items"], item, depth + 1) for item in value
        )
    valid = (
        kind == "string"
        and isinstance(value, str)
        or kind == "boolean"
        and isinstance(value, bool)
        or kind == "integer"
        and type(value) is int
        or kind == "number"
        and type(value) in (int, float)
        and (type(value) is int or isfinite(value))
    )
    if not valid:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    for bound in ("minimum", "maximum", "minLength", "maxLength"):
        if bound not in schema:
            continue
        actual = len(value) if bound.endswith("Length") else value  # type: ignore[arg-type]
        if bound in ("minimum", "minLength") and actual < schema[bound]:  # type: ignore[operator]
            return False
        if bound in ("maximum", "maxLength") and actual > schema[bound]:  # type: ignore[operator]
            return False
    return True


class SubscriptionOperationsService(SubscriptionOperations):
    """Issuer-fenced command operations and pure per-subscription matcher."""

    def __init__(
        self,
        *,
        issuer: ContextIssuer,
        admission: AdmissionPort,
        registry: Registry,
        resolver: TrustedConversationResolver,
        cadence: CadencePolicy,
        subscriptions: SubscriptionStore,
        lifecycle: SubscriptionLifecycleRepository,
        jobs: SubscriptionJobRepository,
        scheduler: SchedulerRepository,
        digest_windows: DigestWindowRepository,
        grants: GrantStore,
        principal_resolver: InvocationPrincipalResolver,
        owner_authority: OwnerRouteProofAuthority | None = None,
        now: Callable[[], datetime],
        max_subscriptions_per_owner: int,
    ) -> None:
        if not isinstance(issuer, ContextIssuer) or not isinstance(registry, Registry):
            raise TypeError("subscription service requires the trusted core runtime")
        for name, port in (
            ("resolver", resolver),
            ("cadence", cadence),
            ("subscriptions", subscriptions),
            ("lifecycle", lifecycle),
            ("jobs", jobs),
            ("scheduler", scheduler),
            ("digest_windows", digest_windows),
            ("grants", grants),
            ("principal_resolver", principal_resolver),
            ("now", now),
            ("admission", admission),
        ):
            if port is None:
                raise TypeError(f"{name} is required")
        if (
            type(max_subscriptions_per_owner) is not int
            or max_subscriptions_per_owner < 1
        ):
            raise ValueError("max_subscriptions_per_owner must be positive")
        self._issuer = issuer
        self._admission = admission
        self._registry = registry
        self._resolver = resolver
        self._cadence = cadence
        self._subscriptions = subscriptions
        self._lifecycle = lifecycle
        self._jobs = jobs
        self._scheduler = scheduler
        self._digest_windows = digest_windows
        self._grants = grants
        if not callable(getattr(principal_resolver, "principal_id", None)):
            raise TypeError("principal resolver is not usable")
        self._principal_resolver = principal_resolver
        if owner_authority is not None and not callable(
            getattr(owner_authority, "require_current", None)
        ):
            raise TypeError("owner authority is not usable")
        self._owner_authority = owner_authority
        self._now = now
        self._max_subscriptions = max_subscriptions_per_owner

    def _command(
        self, invocation: InvocationView
    ) -> tuple[InvocationView, RegisteredModule]:
        view = self._issuer.require(invocation)
        lease = self._issuer.lease_for(view)
        if not isinstance(lease, AdmissionLease):
            raise SubscriptionOperationError()
        if (
            view.origin is not InvocationOrigin.COMMAND
            or view.parent_id is not None
            or view.actor_id is None
            or view.conversation_id is None
            or view.adapter_id is None
        ):
            raise SubscriptionOperationError()
        try:
            snapshot = self._registry.snapshot()
            module = snapshot.module(view.module_id)
        except Exception:
            raise SubscriptionOperationError() from None
        if not module.enabled or module.epoch != view.module_epoch:
            raise SubscriptionOperationError()
        if (
            view.capability_id is None
            or lease.module_id != view.module_id
            or lease.module_epoch != view.module_epoch
            or lease.capability_id != view.capability_id
            or not any(
                command.capability_id == view.capability_id
                for command in module.manifest.commands
            )
            or any(
                tool.capability_id == view.capability_id
                for tool in module.manifest.tools
            )
        ):
            raise SubscriptionOperationError()
        capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == view.capability_id
            ),
            None,
        )
        if (
            capability is None
            or capability.invocation_policy is not InvocationPolicy.COMMAND_ONLY
        ):
            raise SubscriptionOperationError()
        if capability.privacy_floor is PrivacyFloor.OWNER and (
            self._owner_authority is None
            or view.grant_id is not None
            or view.grant_revision is not None
        ):
            raise SubscriptionOperationError()
        try:
            self._admission.check(lease)
        except Exception:
            raise SubscriptionOperationError() from None
        return view, module

    def _check_command_current(self, invocation: InvocationView) -> None:
        view = self._issuer.require(invocation)
        lease = self._issuer.lease_for(view)
        if not isinstance(lease, AdmissionLease):
            raise SubscriptionOperationError()
        try:
            self._admission.check(lease)
            module = self._registry.snapshot().module(view.module_id)
        except Exception:
            raise SubscriptionOperationError() from None
        if not module.enabled or module.epoch != view.module_epoch:
            raise SubscriptionOperationError()

    async def _recipient(
        self, invocation: InvocationView, owner_scope: OwnershipKind
    ) -> ConversationRef:
        if owner_scope is OwnershipKind.AUTHORIZED:
            from ..api.services import resolve_authorized_recipient

            resolved = await resolve_authorized_recipient(invocation, self._resolver)
            if resolved.conversation is None:
                raise PrivateRecipientRequired()
            return resolved.conversation
        try:
            conversation = await self._resolver.resolve(invocation)
        except Exception:
            conversation = None
        if (
            not isinstance(conversation, ConversationRef)
            or conversation.adapter_id != invocation.adapter_id
            or conversation.conversation_id != invocation.conversation_id
        ):
            if owner_scope is not OwnershipKind.PUBLIC:
                raise PrivateRecipientRequired()
            raise SubscriptionOperationError("conversation route is unavailable")
        if (
            owner_scope is OwnershipKind.USER
            and conversation.kind is not ConversationKind.DIRECT
        ):
            raise PrivateRecipientRequired()
        return conversation

    def _current_time(self) -> datetime:
        try:
            current = self._now()
        except Exception:
            raise SubscriptionOperationError("current time is unavailable") from None
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
            or current.utcoffset().total_seconds() != 0
        ):
            raise SubscriptionOperationError("current time is invalid")
        return current.astimezone(UTC)

    async def _validate_grant(
        self, reference: GrantReference, *, owner_id: str, module_id: str
    ) -> GrantReference:
        try:
            grant = await self._grants.current_grant(reference.grant_id)
        except Exception:
            grant = None
        now = self._current_time()
        if (
            not isinstance(grant, Grant)
            or grant.grant_id != reference.grant_id
            or grant.status is not GrantStatus.ACTIVE
            or grant.revision != reference.revision
            or grant.principal_id != owner_id
            or grant.module_id != module_id
            or (
                grant.expires_at is not None and grant.expires_at.astimezone(UTC) <= now
            )
        ):
            raise SubscriptionOperationError("authorization is no longer active")
        return GrantReference(grant.grant_id, grant.revision)

    async def _require_grant(
        self, invocation: InvocationView, scope: OwnershipKind, *, owner_id: str
    ) -> GrantReference | None:
        if scope is not OwnershipKind.AUTHORIZED:
            return None
        if invocation.grant_id is None or invocation.grant_revision is None:
            raise SubscriptionOperationError()
        reference = GrantReference(invocation.grant_id, invocation.grant_revision)
        return await self._validate_grant(
            reference,
            owner_id=owner_id,
            module_id=invocation.module_id,
        )

    async def _principal_id(self, invocation: InvocationView) -> str:
        self._check_command_current(invocation)
        try:
            module = self._registry.snapshot().module(invocation.module_id)
            capability = next(
                item
                for item in module.manifest.capabilities
                if item.capability_id == invocation.capability_id
            )
        except Exception:
            raise SubscriptionOperationError() from None
        if capability.privacy_floor is PrivacyFloor.OWNER:
            try:
                principal_id = await self._owner_authority.require_current(invocation)
            except Exception:
                raise SubscriptionOperationError() from None
            self._check_command_current(invocation)
            return principal_id
        try:
            principal_id = await self._principal_resolver.principal_id(invocation)
        except (PrincipalResolutionDenied, PrincipalResolutionUnavailable):
            raise SubscriptionOperationError() from None
        except Exception:
            raise SubscriptionOperationError() from None
        self._check_command_current(invocation)
        return principal_id

    async def _owner_proof_current(self, invocation: InvocationView) -> str:
        try:
            if self._owner_authority is None:
                raise SubscriptionOperationError()
            principal_id = await self._owner_authority.require_current(invocation)
        except Exception:
            raise SubscriptionOperationError() from None
        self._check_command_current(invocation)
        return principal_id

    @staticmethod
    def _owner_floor(module: RegisteredModule, invocation: InvocationView) -> bool:
        return any(
            item.capability_id == invocation.capability_id
            and item.privacy_floor is PrivacyFloor.OWNER
            for item in module.manifest.capabilities
        )

    async def _require_record_grant(self, record: SubscriptionRecord) -> None:
        if record.grant is None:
            return
        await self._validate_grant(
            record.grant, owner_id=record.owner_id, module_id=record.module_id
        )

    async def _current_link(
        self, record: SubscriptionRecord
    ) -> SubscriptionJobAssociation | None:
        links = await self._jobs.for_collection(record.collection_key)
        return next(
            (link for link in links if link.subscription_id == record.subscription_id),
            None,
        )

    @staticmethod
    def _declarations(module: RegisteredModule, type_id: str):
        manifest: ModuleManifest = module.manifest
        descriptor = next(
            (item for item in manifest.subscriptions if item.type_id == type_id), None
        )
        if descriptor is None:
            raise SubscriptionOperationError("subscription type is not declared")
        schedule = next(
            (
                item
                for item in manifest.schedules
                if item.collector_id == descriptor.collector_id
            ),
            None,
        )
        if schedule is None or schedule.trigger.value != "periodic":
            raise SubscriptionOperationError("subscription collection is unavailable")
        return descriptor, schedule

    async def _build_record(
        self,
        invocation: InvocationView,
        module: RegisteredModule,
        request: SubscriptionRequest,
        *,
        subscription_id: str,
        revision: int,
    ) -> tuple[SubscriptionRecord, SubscriptionJobAssociation]:
        owner = await self._principal_id(invocation)
        descriptor, schedule = self._declarations(module, request.type_id)
        if self._owner_floor(module, invocation) and (
            schedule.shared_scope is OwnershipKind.AUTHORIZED
        ):
            raise SubscriptionOperationError()
        if (
            request.notification_mode not in ("instant", "digest")
            or request.notification_mode not in descriptor.notification_modes
        ):
            raise SubscriptionOperationError("notification mode is not supported")
        if request.notification_mode == "digest":
            if request.digest_schedule is None:
                raise SubscriptionOperationError("digest schedule is required")
        elif request.digest_schedule is not None:
            raise SubscriptionOperationError(
                "instant subscriptions cannot carry a digest schedule"
            )
        if not _schema_matches(descriptor.filter_schema, request.filters):
            raise SubscriptionOperationError("subscription filters are invalid")
        if not _schema_matches(schedule.input_schema, request.collector_parameters):
            raise SubscriptionOperationError("collector parameters are invalid")
        grant = await self._require_grant(
            invocation, schedule.shared_scope, owner_id=owner
        )
        self._check_command_current(invocation)
        recipient = await self._recipient(invocation, schedule.shared_scope)
        self._check_command_current(invocation)
        scope = (
            OwnerScope.public()
            if schedule.shared_scope is OwnershipKind.PUBLIC
            else OwnerScope.user(owner)
            if schedule.shared_scope is OwnershipKind.USER
            else OwnerScope.authorized(owner, grant)  # type: ignore[arg-type]
        )
        collector = module.handlers.collectors.get(schedule.collector_id)
        if collector is None:
            raise SubscriptionOperationError("subscription collector is unavailable")
        try:
            normalized = collector.normalize(request.collector_parameters)
        except Exception:
            raise SubscriptionOperationError(
                "collector parameters are invalid"
            ) from None
        if inspect.isawaitable(normalized) or not isinstance(
            normalized, NormalizedInput
        ):
            if inspect.iscoroutine(normalized):
                normalized.close()
            raise SubscriptionOperationError("collector parameters are invalid")
        key = CollectionKey(
            invocation.module_id,
            schedule.collector_id,
            schedule.key_version,
            schedule.source_id,
            normalized,
            scope,
        )
        try:
            cadence_seconds, config_revision = self._cadence.resolve(
                invocation.module_id, schedule
            )
        except Exception:
            raise SubscriptionOperationError(
                "collection cadence is unavailable"
            ) from None
        if (
            type(cadence_seconds) not in (int, float)
            or not isfinite(cadence_seconds)
            or cadence_seconds < schedule.minimum_interval_seconds
            or type(config_revision) is not int
            or config_revision < 1
        ):
            raise SubscriptionOperationError("collection cadence is invalid")
        record = SubscriptionRecord(
            subscription_id,
            revision,
            invocation.module_id,
            key,
            owner,
            grant,
            recipient,
            request.notification_mode,
            request.filters,
            SubscriptionStatus.ACTIVE,
            request.type_id,
            request.digest_schedule,
        )
        association = SubscriptionJobAssociation(
            subscription_id,
            revision,
            key,
            float(cadence_seconds),
            config_revision,
            revision,
        )
        return record, association

    @staticmethod
    def _view(record: SubscriptionRecord) -> SubscriptionView:
        return SubscriptionView(
            record.subscription_id,
            record.revision,
            record.owner_id,
            record.grant,
            record.recipient.conversation_id,
            record.filters,
        )

    async def create_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        view, module = self._command(invocation)
        if not isinstance(request, SubscriptionRequest):
            raise TypeError("request must be a SubscriptionRequest")
        if request.subscription_id is not None:
            raise ValueError("create request cannot contain a subscription ID")
        owner_floor = self._owner_floor(module, view)
        owner_id = await self._principal_id(view)
        current = await self._subscriptions.list_for_owner(
            owner_id, limit=self._max_subscriptions + 1
        )
        self._check_command_current(view)
        if len(current) >= self._max_subscriptions:
            raise SubscriptionOperationError("subscription limit reached")
        record, association = await self._build_record(
            view,
            module,
            request,
            subscription_id=secrets.token_urlsafe(18),
            revision=1,
        )
        await self._require_record_grant(record)
        self._check_command_current(view)
        async with self._admission.mutation(f"subscription:create:{view.module_id}"):
            self._check_command_current(view)
            owner_id = await self._principal_id(view)
            if owner_id != record.owner_id:
                raise SubscriptionOperationError("identity changed")
            current = await self._subscriptions.list_for_owner(
                owner_id, limit=self._max_subscriptions + 1
            )
            self._check_command_current(view)
            if len(current) >= self._max_subscriptions:
                raise SubscriptionOperationError("subscription limit reached")
            await self._require_record_grant(record)
            self._check_command_current(view)
            current_recipient = await self._recipient(
                view, record.collection_key.scope.kind
            )
            self._check_command_current(view)
            if current_recipient != record.recipient:
                raise PrivateRecipientRequired()
            if owner_floor:
                await self._owner_proof_current(view)
            module_snapshot = self._registry.snapshot()
            live_module = module_snapshot.module(view.module_id)
            if not live_module.enabled or live_module.epoch != view.module_epoch:
                raise SubscriptionOperationError()
            initial_run = CollectionRunRequest(
                association.collection_key,
                self._current_time(),
                association.cadence_seconds,
                association.config_revision,
                live_module.epoch,
                module_snapshot.revision,
            )
            saved = await self._lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.CREATE,
                    record,
                    association,
                    None,
                    None,
                ),
                initial_run=initial_run,
            )
            self._check_command_current(view)
            if owner_floor:
                await self._owner_proof_current(view)
        return self._view(saved)

    async def revise_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        view, module = self._command(invocation)
        if (
            not isinstance(request, SubscriptionRequest)
            or request.subscription_id is None
            or request.expected_revision is None
        ):
            raise ValueError("revise request requires a subscription ID and revision")
        owner_floor = self._owner_floor(module, view)
        current = await self._subscriptions.current(request.subscription_id)
        self._check_command_current(view)
        principal_id = await self._principal_id(view)
        if not self._owns(view, current, principal_id, owner_floor=owner_floor):
            raise SubscriptionOperationError()
        assert current is not None
        await self._require_record_grant(current)
        self._check_command_current(view)
        if current.grant is not None:
            await self._require_existing_authorized_route(view, current)
            self._check_command_current(view)
        if current.revision != request.expected_revision:
            raise RevisionConflict(
                "subscription", request.expected_revision, current.revision
            )
        link = await self._current_link(current)
        self._check_command_current(view)
        if link is None or link.subscription_revision != current.revision:
            raise SubscriptionOperationError("subscription job is unavailable")
        record, association = await self._build_record(
            view,
            module,
            request,
            subscription_id=current.subscription_id,
            revision=current.revision + 1,
        )
        if current.grant is not None and record.recipient != current.recipient:
            raise PrivateRecipientRequired()
        if record.grant != current.grant:
            await self._require_record_grant(record)
            self._check_command_current(view)
        await self._require_record_grant(current)
        self._check_command_current(view)
        async with self._admission.mutation(f"subscription:revise:{view.module_id}"):
            self._check_command_current(view)
            principal_id = await self._principal_id(view)
            if principal_id != record.owner_id:
                raise SubscriptionOperationError("identity changed")
            latest = await self._subscriptions.current(request.subscription_id)
            self._check_command_current(view)
            if not self._owns(view, latest, principal_id, owner_floor=owner_floor):
                raise SubscriptionOperationError()
            assert latest is not None
            if latest.revision != request.expected_revision:
                raise RevisionConflict(
                    "subscription", request.expected_revision, latest.revision
                )
            if latest != current:
                raise SubscriptionOperationError("subscription changed")
            await self._require_record_grant(latest)
            self._check_command_current(view)
            if latest.grant is not None:
                await self._require_existing_authorized_route(view, latest)
                self._check_command_current(view)
            latest_link = await self._current_link(latest)
            self._check_command_current(view)
            if (
                latest_link is None
                or latest_link.subscription_revision != latest.revision
            ):
                raise SubscriptionOperationError("subscription job is unavailable")
            await self._require_record_grant(record)
            self._check_command_current(view)
            current_recipient = await self._recipient(
                view, record.collection_key.scope.kind
            )
            self._check_command_current(view)
            if current_recipient != record.recipient:
                raise PrivateRecipientRequired()
            if owner_floor:
                await self._owner_proof_current(view)
            module_snapshot = self._registry.snapshot()
            live_module = module_snapshot.module(view.module_id)
            if not live_module.enabled or live_module.epoch != view.module_epoch:
                raise SubscriptionOperationError()
            initial_run = CollectionRunRequest(
                association.collection_key,
                self._current_time(),
                association.cadence_seconds,
                association.config_revision,
                live_module.epoch,
                module_snapshot.revision,
            )
            saved = await self._lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.REVISE,
                    record,
                    association,
                    latest.revision,
                    latest_link.association_revision,
                ),
                initial_run=initial_run,
            )
            self._check_command_current(view)
            if owner_floor:
                await self._owner_proof_current(view)
        return self._view(saved)

    async def list_current(
        self, invocation: InvocationView
    ) -> tuple[SubscriptionView, ...]:
        view, module = self._command(invocation)
        owner_floor = self._owner_floor(module, view)
        owner_id = await self._principal_id(view)
        recipient = (
            None if owner_floor else await self._recipient(view, OwnershipKind.PUBLIC)
        )
        self._check_command_current(view)
        records = await self._subscriptions.list_for_owner(
            owner_id, limit=self._max_subscriptions
        )
        self._check_command_current(view)
        output = []
        for record in records:
            if (
                record.module_id != view.module_id
                or (not owner_floor and record.recipient != recipient)
                or record.status is not SubscriptionStatus.ACTIVE
                or record.collection_key.scope.user_id not in (None, owner_id)
                or (
                    owner_floor
                    and (
                        record.owner_id != owner_id
                        or record.collection_key.scope.kind
                        not in (OwnershipKind.PUBLIC, OwnershipKind.USER)
                        or record.grant is not None
                    )
                )
            ):
                continue
            try:
                await self._require_record_grant(record)
                self._check_command_current(view)
            except SubscriptionOperationError:
                continue
            output.append(self._view(record))
        self._check_command_current(view)
        if owner_floor:
            await self._owner_proof_current(view)
        return tuple(output)

    def _owns(
        self,
        invocation: InvocationView,
        record: SubscriptionRecord | None,
        principal_id: str,
        *,
        owner_floor: bool = False,
    ) -> bool:
        base = bool(
            record is not None
            and record.status is SubscriptionStatus.ACTIVE
            and record.owner_id == principal_id
            and record.module_id == invocation.module_id
        )
        if not base or record is None:
            return False
        if owner_floor:
            return (
                record.collection_key.scope.kind
                in (OwnershipKind.PUBLIC, OwnershipKind.USER)
                and record.grant is None
                and record.collection_key.scope.user_id in (None, principal_id)
            )
        return (
            record.recipient.adapter_id == invocation.adapter_id
            and record.recipient.conversation_id == invocation.conversation_id
        )

    async def _require_existing_authorized_route(
        self, invocation: InvocationView, record: SubscriptionRecord
    ) -> None:
        """Require the current trusted route to equal the saved AUTHORIZED route."""
        recipient = await self._recipient(invocation, OwnershipKind.AUTHORIZED)
        if recipient != record.recipient:
            raise PrivateRecipientRequired()

    async def cancel(
        self,
        invocation: InvocationView,
        subscription_id: str,
        *,
        expected_revision: int,
    ) -> None:
        view, module = self._command(invocation)
        owner_floor = self._owner_floor(module, view)
        current = await self._subscriptions.current(subscription_id)
        self._check_command_current(view)
        principal_id = await self._principal_id(view)
        if not self._owns(view, current, principal_id, owner_floor=owner_floor):
            raise SubscriptionOperationError()
        assert current is not None
        await self._require_record_grant(current)
        self._check_command_current(view)
        if current.grant is not None:
            await self._require_existing_authorized_route(view, current)
            self._check_command_current(view)
        if current.revision != expected_revision:
            raise RevisionConflict("subscription", expected_revision, current.revision)
        link = await self._current_link(current)
        self._check_command_current(view)
        if link is None or link.subscription_revision != current.revision:
            raise SubscriptionOperationError("subscription job is unavailable")
        await self._require_record_grant(current)
        self._check_command_current(view)
        cancelled = SubscriptionRecord(
            current.subscription_id,
            current.revision + 1,
            current.module_id,
            current.collection_key,
            current.owner_id,
            current.grant,
            current.recipient,
            current.notification_mode,
            current.filters,
            SubscriptionStatus.CANCELLED,
            current.type_id,
            current.digest_schedule,
        )
        async with self._admission.mutation(f"subscription:cancel:{view.module_id}"):
            self._check_command_current(view)
            principal_id = await self._principal_id(view)
            if principal_id != current.owner_id:
                raise SubscriptionOperationError("identity changed")
            latest = await self._subscriptions.current(subscription_id)
            self._check_command_current(view)
            if not self._owns(view, latest, principal_id, owner_floor=owner_floor):
                raise SubscriptionOperationError()
            assert latest is not None
            if latest != current:
                raise SubscriptionOperationError("subscription changed")
            if latest.revision != expected_revision:
                raise RevisionConflict(
                    "subscription", expected_revision, latest.revision
                )
            await self._require_record_grant(latest)
            self._check_command_current(view)
            if latest.grant is not None:
                await self._require_existing_authorized_route(view, latest)
                self._check_command_current(view)
            latest_link = await self._current_link(latest)
            self._check_command_current(view)
            if (
                latest_link is None
                or latest_link.subscription_revision != latest.revision
            ):
                raise SubscriptionOperationError("subscription job is unavailable")
            if owner_floor:
                await self._owner_proof_current(view)
            await self._lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.CANCEL,
                    cancelled,
                    None,
                    latest.revision,
                    latest_link.association_revision,
                )
            )
            self._check_command_current(view)
            if owner_floor:
                await self._owner_proof_current(view)

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

    async def prepare_evaluations(
        self, lease: ExecutionLease, observation: Observation
    ) -> ObservationEvaluationCommit:
        """Prepare subscription commits for the scheduler's live lease.

        This method performs scoped reads and synchronous evaluator calls only.
        The scheduler must commit the returned value once, together with this
        exact observation and lease, using ``commit_observation_with_evaluations``.
        """
        if lease.key != observation.key:
            raise SubscriptionOperationError(
                "collection lease does not match observation"
            )
        try:
            snapshot = self._registry.snapshot()
            module = snapshot.module(observation.key.module_id)
        except Exception:
            raise SubscriptionOperationError(
                "collection module is unavailable"
            ) from None
        if (
            not module.enabled
            or snapshot.revision != lease.registry_revision
            or module.epoch != lease.module_epoch
        ):
            raise SubscriptionOperationError("collection lease is no longer current")
        if observation.completeness is ObservationCompleteness.FAILED:
            return ObservationEvaluationCommit(observation, ())
        links = await self._jobs.for_collection(observation.key)
        commits: list[SubscriptionEvaluationCommit] = []
        cursor = ObservationCursor(
            observation.observation_id,
            observation.data_version,
            observation.completeness,
            observation.covered_ids,
        )
        for link in links:
            if link.collection_key != observation.key:
                continue
            snapshot_data = await self._scheduler.current_evaluation(
                link.subscription_id, observation.key
            )
            if snapshot_data is None:
                continue
            record = snapshot_data.subscription
            if (
                record.status is not SubscriptionStatus.ACTIVE
                or record.revision != link.subscription_revision
                or record.collection_key != observation.key
                or record.owner_id != (observation.key.scope.user_id or record.owner_id)
            ):
                continue
            if record.grant is not None:
                try:
                    await self._require_record_grant(record)
                except Exception:
                    continue
            descriptor, schedule = self._record_declarations(module, record)
            if descriptor is None or schedule is None:
                continue
            if (
                record.collection_key.collector_id != schedule.collector_id
                or record.collection_key.key_version != schedule.key_version
                or record.collection_key.source_id != schedule.source_id
                or record.collection_key.scope.kind is not schedule.shared_scope
            ):
                continue
            evaluator = module.handlers.evaluators.get(descriptor.matcher_id)
            if evaluator is None:
                continue
            subscription_view = self._view(record)
            try:
                decision = evaluator.evaluate(
                    subscription_view, observation, snapshot_data.state
                )
            except Exception:
                raise SubscriptionOperationError(
                    "subscription evaluation failed"
                ) from None
            if inspect.isawaitable(decision):
                if inspect.iscoroutine(decision):
                    decision.close()
                raise SubscriptionOperationError(
                    "subscription evaluator must be synchronous"
                )
            try:
                decision = validate_evaluation_decision(
                    subscription_view, observation, decision
                )
            except Exception:
                raise SubscriptionOperationError(
                    "subscription evaluation is invalid"
                ) from None
            next_state = EvaluationState(
                1 if snapshot_data.state is None else snapshot_data.state.revision + 1,
                decision.state,
            )
            events: tuple[DeliveryEvent, ...] = ()
            digest_members: tuple[DigestMemberAssociation, ...] = ()
            if decision.triggered:
                assert decision.event_key is not None
                assert decision.event_version is not None
                assert decision.display_data is not None
                event = DeliveryEvent(
                    decision.event_key,
                    decision.event_version,
                    record.subscription_id,
                    record.revision,
                    record.owner_id,
                    record.grant,
                    record.recipient,
                    decision.display_data,
                    delivery_idempotency_key(
                        decision.event_key,
                        decision.event_version,
                        record.subscription_id,
                        record.revision,
                        record.recipient,
                    ),
                    DeliveryState.PENDING,
                )
                events = (event,)
                if record.notification_mode == "digest":
                    if record.digest_schedule is None:
                        raise SubscriptionOperationError(
                            "digest schedule is unavailable"
                        )
                    window = await self._digest_windows.for_schedule(
                        DigestWindowSelector(
                            record.digest_schedule,
                            record.recipient,
                            observation.collected_at.astimezone(UTC),
                        )
                    )
                    if (
                        window is None
                        or window.schedule_profile != record.digest_schedule
                        or window.schedule_recipient != record.recipient
                    ):
                        raise DigestWindowUnavailable()
                    member = DigestMember(
                        record.subscription_id,
                        record.revision,
                        event.event_key,
                        event.event_version,
                    )
                    digest_members = (
                        DigestMemberAssociation(
                            window.window_id, record.recipient, member, event
                        ),
                    )
                    await self._require_record_grant(record)
            commits.append(
                SubscriptionEvaluationCommit(
                    record.subscription_id,
                    record.revision,
                    None
                    if snapshot_data.state is None
                    else snapshot_data.state.revision,
                    next_state,
                    cursor,
                    events,
                    digest_members,
                )
            )
        return ObservationEvaluationCommit(observation, tuple(commits))

    @staticmethod
    def _record_declarations(module: RegisteredModule, record: SubscriptionRecord):
        if record.type_id is None:
            return None, None
        descriptor = next(
            (
                item
                for item in module.manifest.subscriptions
                if item.type_id == record.type_id
            ),
            None,
        )
        if descriptor is None:
            return None, None
        schedule = next(
            (
                item
                for item in module.manifest.schedules
                if item.collector_id == descriptor.collector_id
            ),
            None,
        )
        return descriptor, schedule


__all__ = [
    "CadencePolicy",
    "DigestWindowUnavailable",
    "PrivateRecipientRequired",
    "SubscriptionOperationError",
    "SubscriptionOperationsService",
    "TrustedConversationResolver",
]
