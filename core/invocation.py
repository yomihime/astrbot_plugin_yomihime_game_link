"""The small public/read-only invocation gateway for Core-B02."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from types import MappingProxyType

from ..api.contexts import InvocationOrigin, InvocationView
from ..api.display import (
    CommandsBlock,
    DisplayDocument,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    Link,
    LinksBlock,
    MetricsBlock,
    MoneyValue,
    NumberValue,
    Privacy,
    SeriesBlock,
    TableBlock,
    TextBlock,
    TimeValue,
    UnknownBlock,
)
from ..api.manifests import (
    CommandDescriptor,
    ModuleManifest,
    PrivacyFloor,
    ToolDescriptor,
)
from ..api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)
from ..api.validation import ParameterError, validate_parameters
from .context_issuer import ContextIssuer
from .lifecycle import LifecycleController, LifecycleError
from .policy import origin_allowed, tool_allowed
from .ports import AdmissionLease, AdmissionPort
from .registry import RegisteredModule, Registry
from .task_scope import ScopeCancelled, ScopeDeadlineExceeded, ScopeStaleError

_MESSAGES = {
    ErrorCode.PARAMETER_ERROR: "invalid parameters",
    ErrorCode.NOT_FOUND: "invocation target is not registered",
    ErrorCode.UNSUPPORTED: "invocation is unsupported",
    ErrorCode.MODULE_UNAVAILABLE: "module is unavailable",
    ErrorCode.UNKNOWN: "invocation failed",
}


def _error(code: ErrorCode, *, privacy: Privacy = Privacy.PUBLIC) -> CapabilityResult:
    """Construct an error without copying input or exception text into output."""

    return CapabilityResult(
        result_id="gateway-error",
        status=ResultStatus.ERROR,
        privacy=privacy,
        error=ErrorDetail(code, _MESSAGES[code]),
    )


class Gateway:
    """Validate and execute exact command/Tool entries through Core admission."""

    def __init__(
        self,
        registry: Registry,
        issuer: ContextIssuer,
        *,
        admission: AdmissionPort | None = None,
        lifecycle: LifecycleController | None = None,
        private_authorizer: Callable[[InvocationView], Awaitable[object]] | None = None,
        owner_authority: object | None = None,
        handler_timeout: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isinstance(registry, Registry):
            raise TypeError("registry must be a Registry")
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("issuer must be a ContextIssuer")
        if (admission is None) != (lifecycle is None):
            raise TypeError("Gateway requires both Admission and Lifecycle")
        if admission is not None and (
            not callable(getattr(admission, "admit", None))
            or not callable(getattr(admission, "check", None))
        ):
            raise TypeError("admission must implement AdmissionPort")
        if lifecycle is not None and (
            not isinstance(lifecycle, LifecycleController)
            or lifecycle.issuer is not issuer
            or lifecycle.admission is not admission
        ):
            raise TypeError("Gateway must use the shared CoreRuntime authorities")
        if private_authorizer is not None and not callable(private_authorizer):
            raise TypeError("private_authorizer must be callable")
        if owner_authority is not None and any(
            not callable(getattr(owner_authority, name, None))
            for name in ("capture", "require_current")
        ):
            raise TypeError("owner authority must support proof capture and checks")
        if (
            isinstance(handler_timeout, bool)
            or not isinstance(handler_timeout, (int, float))
            or handler_timeout <= 0
        ):
            raise ValueError("handler_timeout must be positive")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._registry = registry
        self._issuer = issuer
        self._admission = admission
        self._lifecycle = lifecycle
        self._private_authorizer = private_authorizer
        self._owner_authority = owner_authority
        self._handler_timeout = float(handler_timeout)
        self._clock = clock

    async def invoke_command(
        self, view: InvocationView, operation_path: str, parameters: object
    ) -> CapabilityResult:
        """Invoke a declared command using a trusted command-origin view."""

        return await self._invoke(
            view,
            operation_path,
            parameters,
            expected_origin=InvocationOrigin.COMMAND,
            tool=False,
        )

    async def invoke_tool(
        self, view: InvocationView, tool_name: str, parameters: object
    ) -> CapabilityResult:
        """Invoke a declared natural-language Tool."""

        return await self._invoke(
            view,
            tool_name,
            parameters,
            expected_origin=InvocationOrigin.LLM_TOOL,
            tool=True,
        )

    async def _invoke(
        self,
        view: InvocationView,
        target: str,
        parameters: object,
        *,
        expected_origin: InvocationOrigin,
        tool: bool,
    ) -> CapabilityResult:
        # ContextIssuer is the authority boundary.  It intentionally receives
        # the original object, rather than a reconstructed DTO.
        try:
            trusted = self._issuer.require(view)
        except Exception:
            return _error(ErrorCode.MODULE_UNAVAILABLE)

        if (
            trusted.parent_id is not None
            or trusted.capability_id is None
            or not origin_allowed(trusted.origin, expected_origin)
        ):
            return _error(ErrorCode.UNSUPPORTED)

        admission = self._admission
        lifecycle = self._lifecycle
        if admission is None or lifecycle is None:
            # There is no public invocation path that bypasses the shared
            # Lifecycle/Admission pair, even for the legacy constructor.
            return _error(ErrorCode.MODULE_UNAVAILABLE)

        try:
            snapshot = self._registry.snapshot()
            module = snapshot.module(trusted.module_id)
        except Exception:
            return _error(ErrorCode.MODULE_UNAVAILABLE)
        if not self._current_view(snapshot, module, trusted):
            return _error(ErrorCode.MODULE_UNAVAILABLE)

        descriptor = self._find_descriptor(module.manifest, target, tool=tool)
        if descriptor is None:
            return _error(ErrorCode.NOT_FOUND)

        capability_id = descriptor.capability_id
        if trusted.capability_id != capability_id:
            return _error(ErrorCode.UNSUPPORTED)
        capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if capability is None:
            return _error(ErrorCode.NOT_FOUND)
        if tool and not tool_allowed(capability):
            return _error(ErrorCode.UNSUPPORTED)

        owner = capability.privacy_floor is PrivacyFloor.OWNER
        private = capability.privacy_floor in (
            PrivacyFloor.PRIVATE,
            PrivacyFloor.OWNER,
        )

        def denied(code: ErrorCode) -> CapabilityResult:
            # PRIVATE commands need a Grant before producing private data, but
            # their fixed authorization failure is safe to report publicly.
            # OWNER errors remain private so a failed identity/route proof can
            # never become a group-visible result.
            return _error(code, privacy=Privacy.PRIVATE if owner else Privacy.PUBLIC)

        try:
            mapped = self._map_parameters(descriptor.parameter_mapping, parameters)
            validated = validate_parameters(capability, mapped)
        except Exception:
            return denied(ErrorCode.PARAMETER_ERROR)

        try:
            handler = module.handlers.capabilities[capability_id]
        except (KeyError, TypeError):
            return denied(ErrorCode.UNKNOWN)

        try:
            existing_lease = self._issuer.lease_for(trusted)
            if existing_lease is None:
                lease = admission.admit(trusted, capability_id)
            elif (
                isinstance(existing_lease, AdmissionLease)
                and existing_lease.invocation_id == trusted.invocation_id
                and existing_lease.module_id == trusted.module_id
                and existing_lease.module_epoch == trusted.module_epoch
                and existing_lease.capability_id == capability_id
            ):
                admission.check(existing_lease)
                lease = existing_lease
            else:
                raise ValueError("invocation has an unrelated lease")
        except Exception:
            return denied(ErrorCode.MODULE_UNAVAILABLE)

        if owner:
            if (
                expected_origin is not InvocationOrigin.COMMAND
                or self._owner_authority is None
                or trusted.grant_id is not None
                or trusted.grant_revision is not None
            ):
                return denied(ErrorCode.MODULE_UNAVAILABLE)
            try:
                await self._owner_authority.capture(trusted, lease)
                self._require_current(trusted, lease)
            except asyncio.CancelledError:
                raise
            except Exception:
                return denied(ErrorCode.MODULE_UNAVAILABLE)
        elif capability.privacy_floor is PrivacyFloor.PRIVATE:
            if (
                expected_origin is not InvocationOrigin.COMMAND
                or self._private_authorizer is None
            ):
                return denied(ErrorCode.MODULE_UNAVAILABLE)
            try:
                grant = await self._private_authorizer(trusted)
                self._require_private_grant(grant, trusted)
                self._require_current(trusted, lease)
            except asyncio.CancelledError:
                raise
            except Exception:
                return denied(ErrorCode.MODULE_UNAVAILABLE)

        try:
            deadline = self._clock() + self._handler_timeout
            if trusted.deadline is not None:
                deadline = min(deadline, trusted.deadline)
            scope = lifecycle.scope(trusted.module_id)
            result = await scope.run(
                handler.invoke(trusted, validated),
                name=f"gateway:{capability_id}",
                deadline_monotonic=deadline,
            )
        except asyncio.CancelledError:
            raise
        except (ScopeCancelled, ScopeDeadlineExceeded, ScopeStaleError, LifecycleError):
            return denied(ErrorCode.MODULE_UNAVAILABLE)
        except Exception:
            return denied(ErrorCode.UNKNOWN)

        # Re-check both authority and static state after an await.  This closes
        # the release/disable race for handlers that complete late.
        try:
            self._require_current(trusted, lease)
            if owner:
                if self._owner_authority is None:
                    raise ValueError("owner proof authority is unavailable")
                await self._owner_authority.require_current(trusted)
                self._require_current(trusted, lease)
            elif capability.privacy_floor is PrivacyFloor.PRIVATE:
                grant = await self._private_authorizer(trusted)
                self._require_private_grant(grant, trusted)
                self._require_current(trusted, lease)
        except Exception:
            return denied(ErrorCode.MODULE_UNAVAILABLE)

        try:
            privacy = Privacy.PRIVATE if private else Privacy.PUBLIC
            return self._valid_result(result, expected_privacy=privacy)
        except Exception:
            return denied(ErrorCode.UNKNOWN)

    def _require_current(self, view: InvocationView, lease: object) -> None:
        assert self._admission is not None and self._lifecycle is not None
        self._issuer.require(view)
        self._admission.check(lease)  # type: ignore[arg-type]
        self._lifecycle.guard(view.module_id, epoch=view.module_epoch, invocation=view)

    @staticmethod
    def _require_private_grant(grant: object, view: InvocationView) -> None:
        from ..api.services import Grant, GrantStatus

        if (
            not isinstance(grant, Grant)
            or grant.status is not GrantStatus.ACTIVE
            or grant.grant_id != view.grant_id
            or grant.revision != view.grant_revision
            or grant.module_id != view.module_id
            or view.actor_id is None
            or grant.expires_at is not None
            and grant.expires_at <= datetime.now(UTC)
        ):
            raise ValueError("private authorization is not current")

    @staticmethod
    def _find_descriptor(
        manifest: ModuleManifest, target: object, *, tool: bool
    ) -> CommandDescriptor | ToolDescriptor | None:
        if not isinstance(target, str):
            return None
        descriptors = manifest.tools if tool else manifest.commands
        key = "name" if tool else "operation_path"
        return next(
            (item for item in descriptors if getattr(item, key) == target), None
        )

    @staticmethod
    def _map_parameters(
        mapping: Mapping[str, str], parameters: object
    ) -> Mapping[str, object]:
        if not isinstance(parameters, Mapping) or any(
            not isinstance(key, str) for key in parameters
        ):
            raise ParameterError("parameters must be an object")
        if set(parameters) - set(mapping):
            raise ParameterError("parameters contain unexpected fields")
        return {mapping[name]: parameters[name] for name in parameters}

    @staticmethod
    def _valid_public_result(result: object) -> CapabilityResult:
        return Gateway._valid_result(result, expected_privacy=Privacy.PUBLIC)

    @staticmethod
    def _valid_result(result: object, *, expected_privacy: Privacy) -> CapabilityResult:
        """Rebuild and re-run result invariants at the handler boundary."""

        if not isinstance(result, CapabilityResult):
            raise TypeError("expected capability result")
        if not isinstance(result.status, ResultStatus):
            raise TypeError("malformed result status")
        if not isinstance(result.privacy, Privacy):
            raise TypeError("malformed result privacy")
        if result.privacy is not expected_privacy:
            raise ValueError("handler result privacy exceeds its declaration")
        if not isinstance(result.provenance, tuple):
            raise TypeError("result provenance must be a tuple")
        if not isinstance(result.timestamps, tuple):
            raise TypeError("result timestamps must be a tuple")
        if not isinstance(result.warnings, tuple):
            raise TypeError("result warnings must be a tuple")

        # CapabilityResult's constructor checks its status matrix and its
        # document/result privacy match.  Reconstructing it catches mutations
        # made after construction (for example via object.__setattr__).
        checked_result = CapabilityResult(
            result_id=result.result_id,
            status=result.status,
            document=Gateway._rebuild_document(result.document),
            model_facts=Gateway._rebuild_facts(result.model_facts),
            provenance=result.provenance,
            timestamps=tuple(Gateway._rebuild_time(item) for item in result.timestamps),
            privacy=result.privacy,
            warnings=result.warnings,
            error=Gateway._rebuild_error(result.error),
            schema_version=result.schema_version,
        )
        document = checked_result.document
        if document is not None and document.privacy is not expected_privacy:
            raise ValueError("document privacy exceeds its declaration")
        return checked_result

    @staticmethod
    def _rebuild_value(value: object, depth: int = 0) -> object:
        """Rebuild every typed display value instead of trusting its fields."""

        if depth > 64:
            raise ValueError("nested display value is too deep")
        if isinstance(value, NumberValue):
            return NumberValue(value.value, value.unit, value.precision)
        if isinstance(value, MoneyValue):
            return MoneyValue(value.value, value.currency, value.precision)
        if isinstance(value, TimeValue):
            return Gateway._rebuild_time(value)
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("display number is not finite")
            return value
        if isinstance(value, Mapping):
            if not isinstance(value, MappingProxyType):
                raise TypeError("display mappings must be immutable")
            rebuilt_mapping = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("display mapping keys must be text")
                rebuilt_mapping[key] = Gateway._rebuild_value(item, depth + 1)
            return rebuilt_mapping
        if isinstance(value, tuple):
            return tuple(Gateway._rebuild_value(item, depth + 1) for item in value)
        raise TypeError("unsupported display value")

    @staticmethod
    def _rebuild_mapping(value: object) -> Mapping[str, object]:
        if not isinstance(value, MappingProxyType):
            raise TypeError("expected mapping")
        if not all(isinstance(key, str) for key in value):
            raise TypeError("mapping keys must be text")
        return {key: Gateway._rebuild_value(item) for key, item in value.items()}

    @staticmethod
    def _rebuild_time(value: object) -> TimeValue:
        if not isinstance(value, TimeValue):
            raise TypeError("expected time value")
        return TimeValue(value.value, value.timezone_name)

    @staticmethod
    def _rebuild_number(value: object) -> NumberValue | MoneyValue:
        rebuilt = Gateway._rebuild_value(value)
        if not isinstance(rebuilt, (NumberValue, MoneyValue)):
            raise TypeError("expected metric value")
        return rebuilt

    @staticmethod
    def _rebuild_link(value: object) -> Link:
        if not isinstance(value, Link):
            raise TypeError("expected link")
        return Link(value.label, value.url)

    @staticmethod
    def _rebuild_grid_item(value: object) -> GridItem:
        if not isinstance(value, GridItem):
            raise TypeError("expected grid item")
        if not isinstance(value.visibility, Privacy):
            raise TypeError("grid visibility must be Privacy")
        return GridItem(
            value.label,
            Gateway._rebuild_value(value.value),
            value.asset_id,
            value.visibility,
        )

    @staticmethod
    def _rebuild_block(value: object) -> object:
        if isinstance(value, TextBlock):
            rebuilt = TextBlock(value.text, value.fallback_text, value.required)
        elif isinstance(value, FieldsBlock):
            rebuilt = FieldsBlock(
                Gateway._rebuild_mapping(value.fields),
                value.fallback_text,
                value.required,
            )
        elif isinstance(value, MetricsBlock):
            metrics = Gateway._rebuild_mapping(value.metrics)
            rebuilt = MetricsBlock(
                {key: Gateway._rebuild_number(item) for key, item in metrics.items()},
                value.fallback_text,
                value.required,
            )
        elif isinstance(value, TableBlock):
            if not isinstance(value.columns, tuple) or not isinstance(
                value.rows, tuple
            ):
                raise TypeError("table sequences must be tuples")
            if any(not isinstance(row, tuple) for row in value.rows):
                raise TypeError("table rows must be tuples")
            rows = tuple(
                tuple(Gateway._rebuild_value(item) for item in row)
                for row in value.rows
            )
            rebuilt = TableBlock(
                value.columns, rows, value.fallback_text, value.required
            )
        elif isinstance(value, ItemGridBlock):
            if not isinstance(value.items, tuple):
                raise TypeError("grid items must be a tuple")
            rebuilt = ItemGridBlock(
                tuple(Gateway._rebuild_grid_item(item) for item in value.items),
                value.fallback_text,
                value.required,
            )
        elif isinstance(value, ImageBlock):
            if not isinstance(value.visibility, Privacy):
                raise TypeError("image visibility must be Privacy")
            rebuilt = ImageBlock(
                value.asset_id,
                value.alt_text,
                value.visibility,
                value.fallback_text,
                value.required,
            )
        elif isinstance(value, SeriesBlock):
            if not isinstance(value.points, tuple):
                raise TypeError("series points must be a tuple")
            if any(not isinstance(point, tuple) for point in value.points):
                raise TypeError("series points must be tuples")
            points = tuple(
                (Gateway._rebuild_time(point[0]), Gateway._rebuild_number(point[1]))
                for point in value.points
            )
            rebuilt = SeriesBlock(points, value.fallback_text, value.required)
        elif isinstance(value, LinksBlock):
            if not isinstance(value.links, tuple):
                raise TypeError("links must be a tuple")
            rebuilt = LinksBlock(
                tuple(Gateway._rebuild_link(link) for link in value.links),
                value.fallback_text,
                value.required,
            )
        elif isinstance(value, CommandsBlock):
            if not isinstance(value.commands, tuple):
                raise TypeError("commands must be a tuple")
            rebuilt = CommandsBlock(value.commands, value.fallback_text, value.required)
        elif isinstance(value, UnknownBlock):
            rebuilt = UnknownBlock(value.kind, value.required, value.fallback_text)
        else:
            raise TypeError("unsupported display block")
        if rebuilt.kind != value.kind:
            raise ValueError("display block kind mismatch")
        return rebuilt

    @staticmethod
    def _rebuild_document(value: object) -> DisplayDocument | None:
        if value is None:
            return None
        if not isinstance(value, DisplayDocument):
            raise TypeError("expected display document")
        if not isinstance(value.ordered_blocks, tuple):
            raise TypeError("display blocks must be a tuple")
        if not isinstance(value.sources, tuple) or not isinstance(
            value.timestamps, tuple
        ):
            raise TypeError("display document sequences must be tuples")
        if not isinstance(value.privacy, Privacy):
            raise TypeError("document privacy must be Privacy")
        blocks = tuple(Gateway._rebuild_block(item) for item in value.ordered_blocks)
        timestamps = tuple(Gateway._rebuild_time(item) for item in value.timestamps)
        return DisplayDocument(
            value.title,
            value.subject,
            blocks,
            value.sources,
            timestamps,
            value.privacy,
            value.schema_version,
        )

    @staticmethod
    def _rebuild_facts(value: object) -> FactDocument | None:
        if value is None:
            return None
        if not isinstance(value, FactDocument):
            raise TypeError("expected fact document")
        if not isinstance(value.sources, tuple):
            raise TypeError("fact sources must be a tuple")
        return FactDocument(
            Gateway._rebuild_mapping(value.facts), value.sources, value.schema_version
        )

    @staticmethod
    def _rebuild_error(value: object) -> ErrorDetail | None:
        if value is None:
            return None
        if not isinstance(value, ErrorDetail):
            raise TypeError("expected error detail")
        if not isinstance(value.code, ErrorCode) or not isinstance(value.message, str):
            raise TypeError("malformed error detail")
        return ErrorDetail(value.code, value.message)

    @staticmethod
    def _current_view(snapshot, module: RegisteredModule, view: InvocationView) -> bool:
        return (
            module.module_id == view.module_id
            and module.epoch == view.module_epoch
            and module.enabled
        )


__all__ = ["Gateway"]
