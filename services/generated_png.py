"""Core-private, current-command-only PNG authority and bounded memory.

This intentionally couples to OutputService's existing private authority policy
at the same service layer. It is not an SDK resource reader or a send permit.
No production composition or sender is supplied. Managed route/Grant mutation
must share Admission's gate; identity mappings retain their append-only contract.
Budgets meter payload and conversion reservations, not Python heap/RSS. Bytes
already handed to a caller cannot be recalled or promised to be zeroed.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from secrets import token_bytes
from time import monotonic

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.display import DisplayAudience, Privacy
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus
from yomihime_game_link_sdk.storage import OwnershipKind

from ..core.admission import AdmissionController
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import AdmissionLease, MessageTarget
from ..presentation.card_bitmap import RenderedBitmap
from ..presentation.png_validation import (
    PngValidationError,
    PngValidationLimits,
    check_png_input,
    validate_png,
)
from .output import OutputService


class GeneratedPngError(Exception):
    """Stable errors; never include upstream exceptions or authority values."""

    def __init__(self, reason: str = "access_denied") -> None:
        self.reason = (
            reason
            if reason
            in (
                "access_denied",
                "invalid_context",
                "resource_expired",
                "image_invalid",
                "image_budget",
                "dependency_missing",
                "closed",
                "busy",
            )
            else "access_denied"
        )
        super().__init__(self.reason)


@dataclass(frozen=True)
class GeneratedPngLimits:
    max_contexts: int = 8
    max_raw_bytes: int = 4_194_304
    max_conversion_bytes: int = 16_777_216
    max_png_bytes: int = 1_048_576
    max_width: int = 2048
    max_height: int = 8192
    max_pixels: int = 8_000_000
    ttl: float = 30.0


class _GeneratedPngContext:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<private generated PNG context>"


@dataclass(frozen=True, eq=False)
class _GeneratedPngRef:
    digest: str
    width: int
    height: int
    size: int
    _handle: bytes = field(default_factory=lambda: token_bytes(32), repr=False)


@dataclass(repr=False)
class _Entry:
    context: _GeneratedPngContext
    invocation: InvocationView
    result: CapabilityResult
    admission: AdmissionLease
    expires: float
    principal: str | None = None
    target: MessageTarget | None = None
    audience: DisplayAudience | None = None
    ownership: OwnershipKind | None = None
    reference: _GeneratedPngRef | None = None
    content: bytes | None = None
    raw: int = 0
    busy: bool = True
    retired: bool = False
    pin_used: bool = False
    pin: _GeneratedPngLease | None = None


class _GeneratedPngLease:
    __slots__ = (
        "_coordinator",
        "_entry",
        "_read",
        "_released",
        "_conversion",
        "_operations",
    )

    def __init__(
        self, coordinator: GeneratedPngCoordinator, entry: _Entry, conversion: int
    ):
        self._coordinator = coordinator
        self._entry = entry
        self._read = False
        self._released = False
        self._conversion = conversion
        self._operations = 0

    def __repr__(self) -> str:
        return "<private generated PNG lease>"

    async def read_once(self) -> bytes:
        return await self._coordinator._read_once(self)

    def release(self) -> None:
        self._coordinator._release_pin(self)


class GeneratedPngCoordinator:
    """Single-loop owner of private contexts, bytes and exact-object leases."""

    def __init__(
        self,
        *,
        output_authority: OutputService,
        limits: GeneratedPngLimits = GeneratedPngLimits(),
        monotonic_clock: Callable[[], float] = monotonic,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            type(output_authority) is not OutputService
            or type(output_authority._admission) is not AdmissionController
        ):
            raise TypeError("generated PNG requires Core output authority")
        if type(limits) is not GeneratedPngLimits:
            raise ValueError("invalid generated PNG limits")
        hard = GeneratedPngLimits()
        for item in fields(limits):
            value = getattr(limits, item.name)
            if item.name == "ttl":
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 < value <= 60
                ):
                    raise ValueError("invalid generated PNG limits")
            elif type(value) is not int or not 1 <= value <= getattr(hard, item.name):
                raise ValueError("invalid generated PNG limits")
        self._loop = asyncio.get_running_loop()
        self._output = output_authority
        self._limits = limits
        self._png_limits = PngValidationLimits(
            limits.max_png_bytes, limits.max_width, limits.max_height, limits.max_pixels
        )
        self._clock = monotonic_clock
        self._utc = utc_clock or (lambda: datetime.now(UTC))
        self._entries: dict[_GeneratedPngContext, _Entry] = {}
        self._raw_bytes = self._conversion_bytes = 0
        self._decoding = False
        self._closed = False
        self._timer: asyncio.TimerHandle | None = None
        self._drained = asyncio.Event()
        self._drained.set()
        self._unsubscribe = self._output._issuer.add_release_observer(self._on_release)

    def __repr__(self) -> str:
        return "<private generated PNG coordinator>"

    def _own_loop(self) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            raise GeneratedPngError("invalid_context") from None
        if current is not self._loop:
            raise GeneratedPngError("invalid_context")

    def _check(self, entry: _Entry) -> None:
        self._own_loop()
        if self._closed:
            raise GeneratedPngError("closed")
        if self._entries.get(entry.context) is not entry or entry.retired:
            raise GeneratedPngError("invalid_context")
        if self._clock() >= entry.expires:
            self._retire(entry)
            raise GeneratedPngError("resource_expired")
        try:
            self._output._issuer.require(entry.invocation)
            if self._output._root_lease(entry.invocation) is not entry.admission:
                raise ValueError
            self._output._admission.check(entry.admission)
            self._output._require_current_module(entry.invocation)
        except Exception:
            raise GeneratedPngError() from None

    def _entry(self, context: _GeneratedPngContext) -> _Entry:
        self._own_loop()
        if type(context) is not _GeneratedPngContext:
            raise GeneratedPngError("invalid_context")
        entry = self._entries.get(context)
        if entry is None:
            raise GeneratedPngError("invalid_context")
        self._check(entry)
        return entry

    async def _authority(self, entry: _Entry, *, capture: bool = False) -> None:
        """Caller holds Admission control; every await is followed by fencing."""
        try:
            self._check(entry)
            principal = await self._output._principal_resolver.principal_id(
                entry.invocation
            )
            self._check(entry)
            target = await self._output._target(entry.invocation, entry.result.privacy)
            self._check(entry)
            expiry = await self._output._validate_command_current(
                entry.invocation, entry.result, target
            )
            self._check(entry)
            now = self._utc()
            if (
                not isinstance(now, datetime)
                or now.tzinfo is None
                or now.utcoffset() is None
            ):
                raise ValueError
            if expiry is not None and expiry <= now:
                raise ValueError
            audience = (
                DisplayAudience.PRIVATE
                if entry.result.privacy is Privacy.PRIVATE
                else DisplayAudience.PUBLIC
            )
            ownership = (
                OwnershipKind.AUTHORIZED
                if entry.invocation.grant_id is not None
                else OwnershipKind.USER
            )
            if capture:
                entry.principal, entry.target = principal, target
                entry.audience, entry.ownership = audience, ownership
            elif (principal, target, audience, ownership) != (
                entry.principal,
                entry.target,
                entry.audience,
                entry.ownership,
            ):
                raise ValueError
        except GeneratedPngError:
            raise
        except Exception:
            raise GeneratedPngError() from None

    async def bind(
        self, invocation: InvocationView, result: CapabilityResult
    ) -> _GeneratedPngContext:
        self._own_loop()
        if self._closed:
            raise GeneratedPngError("closed")
        self.expire()
        try:
            validate_contract(invocation)
            validate_contract(result)
            self._output._issuer.require(invocation)
            capability = self._output._require_current_module(invocation)
            lease = self._output._root_lease(invocation)
            self._output._admission.check(lease)
            if (
                type(result) is not CapabilityResult
                or invocation.origin is not InvocationOrigin.COMMAND
                or invocation.parent_id is not None
                or not invocation.actor_id
                or capability is None
                or result.status is ResultStatus.ERROR
                or result.document is None
            ):
                raise ValueError
        except Exception:
            raise GeneratedPngError() from None
        if len(self._entries) >= self._limits.max_contexts:
            raise GeneratedPngError("image_budget")
        context = _GeneratedPngContext()
        entry = _Entry(
            context, invocation, result, lease, self._clock() + self._limits.ttl
        )
        self._entries[context] = entry
        self._drained.clear()
        self._schedule()
        try:
            async with self._output._admission.mutation("generated-png:bind"):
                await self._authority(entry, capture=True)
            return context
        except BaseException:
            self._retire(entry)
            raise
        finally:
            entry.busy = False
            self._drop(entry)

    async def register(
        self, context: _GeneratedPngContext, bitmap: RenderedBitmap
    ) -> _GeneratedPngRef:
        entry = self._entry(context)
        if entry.busy or entry.reference is not None:
            raise GeneratedPngError("busy")
        try:
            check_png_input(bitmap, self._png_limits)
        except PngValidationError as error:
            raise GeneratedPngError(error.reason) from None
        size = len(bitmap.content)
        if self._raw_bytes + size > self._limits.max_raw_bytes:
            raise GeneratedPngError("image_budget")
        entry.busy, entry.raw = True, size
        self._raw_bytes += size
        try:
            async with self._output._admission.mutation("generated-png:register"):
                await self._authority(entry)
                if self._decoding:
                    raise GeneratedPngError("busy")
                self._decoding = True
                try:
                    metadata = validate_png(bitmap, limits=self._png_limits)
                except PngValidationError as error:
                    raise GeneratedPngError(error.reason) from None
                finally:
                    self._decoding = False
                self._check(entry)
                reference = _GeneratedPngRef(
                    metadata.digest, metadata.width, metadata.height, metadata.size
                )
                entry.reference, entry.content = reference, bitmap.content
                return reference
        finally:
            entry.busy = False
            if entry.reference is None:
                self._raw_bytes -= entry.raw
                entry.raw = 0
            self._drop(entry)

    @asynccontextmanager
    async def pin(
        self,
        context: _GeneratedPngContext,
        reference: _GeneratedPngRef,
        *,
        invocation: InvocationView,
        result: CapabilityResult,
        expected_target: MessageTarget,
        audience: DisplayAudience,
    ) -> AsyncIterator[_GeneratedPngLease]:
        entry = self._entry(context)
        if (
            entry.invocation is not invocation
            or entry.result is not result
            or entry.reference is not reference
            or reference is None
            or entry.target != expected_target
            or entry.audience is not audience
        ):
            raise GeneratedPngError()
        if entry.busy or entry.pin_used:
            raise GeneratedPngError("busy")
        conversion = 3 * (4 * ((entry.raw + 2) // 3))
        if self._conversion_bytes + conversion > self._limits.max_conversion_bytes:
            raise GeneratedPngError("image_budget")
        lease = _GeneratedPngLease(self, entry, conversion)
        entry.pin_used = True
        entry.pin = lease
        self._conversion_bytes += conversion
        try:
            async with self._output._admission.mutation("generated-png:pin"):
                await self._authority(entry)
            self._lease(lease)
            yield lease
        finally:
            lease.release()

    def _lease(self, lease: _GeneratedPngLease) -> _Entry:
        self._own_loop()
        if (
            type(lease) is not _GeneratedPngLease
            or lease._coordinator is not self
            or lease._released
        ):
            raise GeneratedPngError("invalid_context")
        entry = lease._entry
        if entry.pin is not lease:
            raise GeneratedPngError("invalid_context")
        self._check(entry)
        return entry

    async def revalidate(self, lease: _GeneratedPngLease) -> None:
        with self._operation(lease) as entry:
            async with self._output._admission.mutation("generated-png:revalidate"):
                await self._authority(entry)
                self._lease(lease)

    async def _read_once(self, lease: _GeneratedPngLease) -> bytes:
        with self._operation(lease) as entry:
            if lease._read:
                raise GeneratedPngError("busy")
            # Reserve the sole read before awaiting; concurrent reads cannot race.
            lease._read = True
            async with self._output._admission.mutation("generated-png:read"):
                await self._authority(entry)
                self._lease(lease)
                if entry.content is None:
                    raise GeneratedPngError("invalid_context")
                return entry.content

    @contextmanager
    def _operation(self, lease: _GeneratedPngLease):
        entry = self._lease(lease)
        lease._operations += 1
        try:
            yield entry
        finally:
            lease._operations -= 1
            if lease._released and lease._operations == 0:
                self._finish_release(lease)

    def release(self, context: _GeneratedPngContext) -> None:
        self._own_loop()
        if type(context) is not _GeneratedPngContext:
            raise GeneratedPngError("invalid_context")
        entry = self._entries.get(context)
        if entry is not None:
            self._retire(entry)

    def _release_pin(self, lease: _GeneratedPngLease) -> None:
        self._own_loop()
        if type(lease) is not _GeneratedPngLease or lease._coordinator is not self:
            raise GeneratedPngError("invalid_context")
        if lease._released:
            return
        entry = lease._entry
        if entry.pin is not lease:
            raise GeneratedPngError("invalid_context")
        lease._released = True
        if lease._operations == 0:
            self._finish_release(lease)

    def _finish_release(self, lease: _GeneratedPngLease) -> None:
        entry = lease._entry
        entry.pin = None
        self._conversion_bytes -= lease._conversion
        # Leases no longer retain an entry (and its PNG) after real release.
        lease._entry = None
        self._drop(entry)

    def _retire(self, entry: _Entry) -> None:
        entry.retired = True
        self._drop(entry)

    def _drop(self, entry: _Entry) -> None:
        if entry.retired and not entry.busy and entry.pin is None:
            if self._entries.get(entry.context) is entry:
                self._entries.pop(entry.context)
                self._raw_bytes -= entry.raw
                entry.raw = 0
                entry.content = None
                entry.reference = None
        if not self._entries:
            self._drained.set()
        self._schedule()

    def _on_release(self, invocation: InvocationView) -> None:
        self._own_loop()
        for entry in tuple(self._entries.values()):
            if entry.invocation is invocation:
                self._retire(entry)

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if not self._closed:
            deadlines = [e.expires for e in self._entries.values() if not e.retired]
            if deadlines:
                self._timer = self._loop.call_later(
                    max(0, min(deadlines) - self._clock()), self.expire
                )

    def expire(self) -> None:
        self._own_loop()
        now = self._clock()
        for entry in tuple(self._entries.values()):
            if now >= entry.expires:
                self._retire(entry)
        self._schedule()

    def close(self) -> bool:
        self._own_loop()
        if not self._closed:
            self._closed = True
            self._unsubscribe()
            for entry in tuple(self._entries.values()):
                self._retire(entry)
            self._schedule()
        return not self._entries

    async def wait_closed(self, timeout: float) -> bool:
        self._own_loop()
        if (
            type(timeout) not in (float, int)
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("invalid close timeout")
        if not self._closed:
            raise GeneratedPngError("closed")
        if not self._entries:
            return True
        try:
            await asyncio.wait_for(self._drained.wait(), timeout)
        except TimeoutError:
            return False
        return True
