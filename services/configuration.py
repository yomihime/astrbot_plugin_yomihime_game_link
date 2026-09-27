"""Scope-bound configuration views and the trusted configuration coordinator.

Modules only receive :class:`ConfigurationService` through its ``current``
method.  ``update`` is a host-side operation: sensitive values cross the
SecretStore port first, and the resulting receipt is claimed before the
SQLite repository CAS.  This module intentionally contains no storage or
encryption implementation.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, Protocol

from ..api.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ..api.manifests import ConfigField
from ..api.services import (
    ConfigPatch,
    ConfigPatchMode,
    ConfigSnapshot,
    ConfigTarget,
    PersistedConfigPatch,
    validate_config_patch,
)
from ..api.storage import (
    ClaimedSecretReceipt,
    SecretMetadataState,
    SecretReceipt,
    SecretReceiptState,
    SecretTarget,
)
from ..core.ports import (
    AdmissionPort,
    ConfigRepository,
    RevisionConflict,
    SecretCompensationState,
    SecretOwner,
    SecretReceiptLedger,
    SecretStore,
    SecretTransition,
)
from ..infrastructure.sqlite.executor import SQLiteExecutorError


class ConfigPublisher(Protocol):
    """Synchronous bridge to the shared capability-health projection."""

    def __call__(
        self,
        module_id: str,
        snapshot: ConfigSnapshot,
        *,
        changed_fields: frozenset[str],
    ) -> tuple[str, ...]: ...


AdminGrantValidator = Callable[[AdminAuthorizationGrant], Awaitable[None]]


class ConfigurationRecoveryRequired(RuntimeError):
    """The metadata result is durable, but secret cleanup needs recovery."""

    code = "configuration_recovery_required"

    def __init__(
        self,
        message: str = "configuration secret cleanup requires recovery",
        *,
        snapshot: ConfigSnapshot | None = None,
        transitions: tuple[object, ...] = (),
    ) -> None:
        super().__init__(message)
        self.snapshot = snapshot
        self.transitions = tuple(transitions)


class ConfigurationUpdateFailed(RuntimeError):
    """A config update did not become a user-visible success."""

    code = "configuration_update_failed"

    def __init__(self, message: str = "configuration update failed") -> None:
        super().__init__(message)


def _bounded_operation_id(value: str | None) -> str:
    if value is None:
        return f"config_{secrets.token_urlsafe(18)}"
    if (
        type(value) is not str
        or not value.strip()
        or any(character.isspace() or character in "/\\" for character in value)
    ):
        raise ValueError("operation_id must be bounded text")
    return value


def _fields(value: Iterable[ConfigField]) -> tuple[ConfigField, ...]:
    fields = tuple(value)
    if not fields or any(not isinstance(item, ConfigField) for item in fields):
        raise ValueError("configuration requires declared fields")
    if len({item.name for item in fields}) != len(fields):
        raise ValueError("configuration fields must be unique")
    return fields


def _repository_target(target: ConfigTarget) -> ConfigTarget:
    return ConfigTarget.validate(target)


class _ConfigurationCoordinator:
    """Trusted host-side configuration coordinator.

    ``fields`` must come from the current module manifest.  The service keeps
    a canonical copy so callers cannot add a field or reinterpret sensitivity
    in a patch.
    """

    __slots__ = (
        "__target",
        "__fields",
        "__repository",
        "__secret_store",
        "__ledger",
        "__admission",
        "__validate_admin_grant",
        "__publish_config",
    )

    def __init__(
        self,
        target: ConfigTarget,
        fields: Iterable[ConfigField],
        repository: ConfigRepository,
        secret_store: SecretStore,
        ledger: SecretReceiptLedger | None = None,
        *,
        admission: AdmissionPort | None = None,
        validate_admin_grant: AdminGrantValidator | None = None,
        publish_config: ConfigPublisher | None = None,
    ) -> None:
        target = ConfigTarget.validate(target)
        fields = _fields(fields)
        if not callable(getattr(repository, "current", None)) or not callable(
            getattr(repository, "update_authorized", None)
        ):
            raise TypeError("configuration repository is not usable")
        if not callable(getattr(secret_store, "stage", None)):
            raise TypeError("secret store is not usable")
        if ledger is None:
            ledger = secret_store  # S3's SQLite implementation provides both ports.
        for method in (
            "claim_for_config",
            "finalize_active",
            "mark_cas_conflict",
            "pending",
        ):
            if not callable(getattr(ledger, method, None)):
                raise TypeError("secret receipt ledger is not usable")
        object.__setattr__(self, "_ConfigurationCoordinator__target", target)
        object.__setattr__(self, "_ConfigurationCoordinator__fields", fields)
        object.__setattr__(self, "_ConfigurationCoordinator__repository", repository)
        object.__setattr__(
            self, "_ConfigurationCoordinator__secret_store", secret_store
        )
        object.__setattr__(self, "_ConfigurationCoordinator__ledger", ledger)
        if admission is not None and not callable(getattr(admission, "mutation", None)):
            raise TypeError("configuration admission is not usable")
        if validate_admin_grant is not None and not callable(validate_admin_grant):
            raise TypeError("admin grant validator must be callable")
        if publish_config is not None and not callable(publish_config):
            raise TypeError("configuration publisher must be callable")
        object.__setattr__(self, "_ConfigurationCoordinator__admission", admission)
        object.__setattr__(
            self,
            "_ConfigurationCoordinator__validate_admin_grant",
            validate_admin_grant,
        )
        object.__setattr__(
            self, "_ConfigurationCoordinator__publish_config", publish_config
        )

    def view(self) -> "ConfigurationService":
        """Return the supported facade for a trusted same-process extension.

        The host retains this coordinator and its admin update capability;
        the facade itself is a declarative API boundary, not a sandbox for
        arbitrary same-process Python reflection.
        """

        return ConfigurationService(self)

    async def current(self) -> ConfigSnapshot:
        """Read only this principal/module's metadata-safe configuration."""

        snapshot = await self.__repository.current(self.__target)
        if not isinstance(snapshot, ConfigSnapshot):
            raise ValueError("configuration repository returned an invalid snapshot")
        if (
            snapshot.target is not None
            and ConfigTarget.validate(snapshot.target) != self.__target
        ):
            raise ValueError("configuration snapshot target does not match service")
        self._validate_snapshot_fields(snapshot)
        return snapshot

    async def update(
        self, patch: ConfigPatch | str, supplied_patch: ConfigPatch | None = None
    ) -> ConfigSnapshot:
        """Reject the legacy unauthenticated write surface."""

        del patch, supplied_patch
        raise AdminAuthorizationDenied from None

    async def clear(
        self, fields: Iterable[str], *, expected_revision: int
    ) -> ConfigSnapshot:
        """Reject the legacy unauthenticated clear surface."""

        del fields, expected_revision
        raise AdminAuthorizationDenied from None

    async def update_admin(
        self,
        target: ConfigTarget,
        patch: ConfigPatch,
        grant: AdminAuthorizationGrant,
    ) -> ConfigSnapshot:
        """Apply an admin patch through the shared mutation and config CAS."""

        target = ConfigTarget.validate(target)
        if target != self.__target:
            raise AdminAuthorizationDenied from None
        if (
            not isinstance(grant, AdminAuthorizationGrant)
            or grant.operation is not AdminOperation.UPDATE_CONFIG
        ):
            raise AdminAuthorizationDenied from None
        if (
            self.__admission is None
            or self.__validate_admin_grant is None
            or self.__publish_config is None
        ):
            raise AdminAuthorizationDenied from None
        return await self._apply_authorized(patch, grant)

    async def _apply_authorized(
        self, patch: ConfigPatch, grant: AdminAuthorizationGrant
    ) -> ConfigSnapshot:
        """Single implementation used by the authenticated host operation."""

        patch = self._canonical_patch(patch)
        target = self.__target
        sensitive = {
            item.field: item for item in patch.updates if item.secret is not None
        }
        old_refs: tuple[tuple[Any, Any], ...] = ()

        receipts: dict[str, SecretReceipt] = {}
        claimed: list[SecretReceipt] = []
        cas_compensated = False
        try:
            for field, update in sensitive.items():
                if update.secret is None:
                    raise ConfigurationUpdateFailed()
                try:
                    receipt = await self.__secret_store.stage(
                        update.secret.value,
                        target=SecretTarget(
                            target.principal_id, target.module_id, field
                        ),
                        operation_id=patch.operation_id or "",
                        expected_config_revision=patch.expected_revision,
                    )
                except Exception as exc:
                    if getattr(
                        exc, "code", None
                    ) == "secret_store_unavailable" or isinstance(
                        exc, SQLiteExecutorError
                    ):
                        raise
                    raise ConfigurationUpdateFailed("secret staging failed") from exc
                receipt = SecretReceipt.validate(receipt)
                if (
                    receipt.state is not SecretReceiptState.STAGED
                    or receipt.target
                    != SecretTarget(target.principal_id, target.module_id, field)
                    or receipt.operation_id != patch.operation_id
                    or receipt.expected_config_revision != patch.expected_revision
                ):
                    raise ConfigurationUpdateFailed()
                receipts[field] = receipt

            # Build the exact persisted shape before claiming.  This ensures
            # any missing/extra receipt is rejected before a repository call.
            persisted = patch.to_persisted(target, receipts)
            persisted = PersistedConfigPatch.validate_for(target, persisted)
            admission = self.__admission
            validate_grant = self.__validate_admin_grant
            publisher = self.__publish_config
            assert admission is not None and validate_grant is not None
            assert publisher is not None
            async with admission.mutation("admin-config-update"):
                await validate_grant(grant)
                # Read the actual predecessor while every in-process config
                # writer is excluded, then keep this same snapshot's refs for
                # post-CAS cleanup. Sensitive staging remains outside the gate.
                old_refs = await self._old_refs(target, patch)
                for field in sensitive:
                    receipt = receipts[field]
                    try:
                        claimed_receipt = await self.__ledger.claim_for_config(
                            receipt,
                            target=receipt.target,
                            operation_id=receipt.operation_id,
                            expected_config_revision=receipt.expected_config_revision,
                            expected_ledger_revision=receipt.ledger_revision,
                        )
                    except RevisionConflict:
                        raise
                    except SQLiteExecutorError:
                        raise
                    except Exception as exc:
                        raise ConfigurationUpdateFailed("secret claim failed") from exc
                    claimed.append(ClaimedSecretReceipt.validate(claimed_receipt))

                async def commit_finalize_publish() -> ConfigSnapshot:
                    nonlocal cas_compensated
                    try:
                        committed = await self.__repository.update_authorized(
                            target, persisted, grant
                        )
                    except SQLiteExecutorError:
                        # Keep the receipt as durable owner while the worker closes.
                        raise
                    except Exception as exc:
                        try:
                            observed = await self.__repository.current(target)
                        except Exception:
                            raise ConfigurationRecoveryRequired(
                                transitions=tuple(
                                    (
                                        *await self._mark_recovery_claims(
                                            claimed, "cas_unknown"
                                        ),
                                        *self._transitions_for_old_refs(old_refs, 0),
                                    )
                                )
                            ) from exc
                        if observed.revision > patch.expected_revision:
                            raise ConfigurationRecoveryRequired(
                                snapshot=observed,
                                transitions=tuple(
                                    (
                                        *await self._mark_recovery_claims(
                                            claimed, "cas_unknown"
                                        ),
                                        *self._transitions_for_old_refs(
                                            old_refs, observed.revision
                                        ),
                                    )
                                ),
                            ) from exc
                        failures = await self._compensate_claimed(
                            claimed, reason="config_cas_failed"
                        )
                        cas_compensated = True
                        if failures:
                            raise ConfigurationRecoveryRequired(
                                transitions=tuple(failures)
                            ) from exc
                        if isinstance(exc, RevisionConflict):
                            raise
                        raise ConfigurationUpdateFailed(
                            "configuration CAS failed"
                        ) from exc

                    try:
                        for claim in claimed:
                            await self.__ledger.finalize_active(
                                claim, metadata_revision=committed.revision
                            )
                    except SQLiteExecutorError:
                        # Startup recovery owns the committed reference.
                        raise
                    except Exception as exc:
                        # Config now references this receipt; preserve it for
                        # startup reconciliation instead of deleting it.
                        raise ConfigurationRecoveryRequired(
                            snapshot=committed,
                            transitions=tuple(
                                self._transition(
                                    claim.secret_ref,
                                    claim.operation_id,
                                    SecretCompensationState.RECOVERABLE,
                                    committed.revision,
                                )
                                for claim in claimed
                            ),
                        ) from exc

                    changed_fields = frozenset(
                        update.field
                        for update in patch.updates
                        if update.mode is not ConfigPatchMode.KEEP
                    )
                    try:
                        publisher(
                            target.module_id,
                            committed,
                            changed_fields=changed_fields,
                        )
                    except Exception as exc:
                        raise ConfigurationRecoveryRequired(snapshot=committed) from exc

                    cleanup = await self._delete_old(old_refs, patch)
                    if cleanup:
                        raise ConfigurationRecoveryRequired(
                            snapshot=committed, transitions=tuple(cleanup)
                        )
                    return committed

                # This locally owned continuation covers the durable CAS,
                # receipt finalization, health publication, and old-secret
                # cleanup. A caller cancellation is delayed until it drains;
                # no detached task or follow-up cleanup job is left behind.
                commit_task = asyncio.create_task(commit_finalize_publish())
                cancellation: asyncio.CancelledError | None = None
                while not commit_task.done():
                    try:
                        await asyncio.shield(commit_task)
                    except asyncio.CancelledError as exc:
                        if cancellation is None:
                            cancellation = exc
                snapshot = commit_task.result()
                if cancellation is not None:
                    raise cancellation
                return snapshot
        except Exception as exc:
            if not isinstance(exc, ConfigurationRecoveryRequired):
                if isinstance(exc, SQLiteExecutorError):
                    # Keep the durable receipt as recovery owner while the
                    # worker is closing; do not enqueue compensation work.
                    raise
                failures: list[object] = []
                if claimed and not cas_compensated:
                    failures.extend(
                        await self._compensate_claimed(
                            claimed, reason="config_update_failed"
                        )
                    )
                claimed_tokens = {item.secret_ref.token for item in claimed}
                failures.extend(
                    await self._compensate_unclaimed(
                        {
                            field: receipt
                            for field, receipt in receipts.items()
                            if receipt.secret_ref.token not in claimed_tokens
                        },
                        reason="config_update_failed",
                    )
                )
                failures.extend(
                    await self._recover_pending_operation(
                        patch,
                        excluded_tokens={
                            item.secret_ref.token for item in receipts.values()
                        },
                    )
                )
                if failures:
                    raise ConfigurationRecoveryRequired(
                        transitions=tuple(failures)
                    ) from exc
            raise

    async def recover_manifest_secrets(self) -> tuple[SecretTransition, ...]:
        """Reconcile this discovered manifest's host-config secret receipts."""

        admission = self.__admission
        if admission is None:
            raise ConfigurationRecoveryRequired(
                "configuration recovery requires the runtime mutation gate"
            )
        fields = tuple(field for field in self.__fields if field.sensitive)
        if not fields:
            return ()

        async with admission.mutation("config-secret-startup-recovery"):
            pending_by_field: dict[str, tuple[SecretReceipt, ...]] = {}
            for field in fields:
                target = SecretTarget(
                    self.__target.principal_id,
                    self.__target.module_id,
                    field.name,
                )
                pending = await self.__ledger.pending(target)
                pending_by_field[field.name] = tuple(
                    SecretReceipt.validate(receipt) for receipt in pending
                )

            try:
                snapshot = await self.current()
            except SQLiteExecutorError:
                raise
            except Exception as exc:
                transitions = tuple(
                    self._recovery_transition(receipt)
                    for receipts in pending_by_field.values()
                    for receipt in receipts
                )
                raise ConfigurationRecoveryRequired(transitions=transitions) from exc

            metadata_by_field = {item.field: item for item in snapshot.secret_metadata}
            unresolved: list[SecretTransition] = []
            for field, receipts in pending_by_field.items():
                for receipt in receipts:
                    metadata = metadata_by_field.get(field)
                    referenced = (
                        metadata is not None
                        and metadata.secret_ref == receipt.secret_ref
                    )
                    exact_claim = (
                        referenced
                        and receipt.state is SecretReceiptState.CLAIMED
                        and receipt.target
                        == SecretTarget(
                            self.__target.principal_id,
                            self.__target.module_id,
                            field,
                        )
                        and receipt.secret_ref.operation_id == receipt.operation_id
                        and metadata.state is SecretMetadataState.ACTIVE
                        and metadata.revision == receipt.expected_config_revision + 1
                        and metadata.revision <= snapshot.revision
                    )
                    if exact_claim:
                        claim = ClaimedSecretReceipt(
                            receipt.secret_ref,
                            receipt.target,
                            receipt.operation_id,
                            receipt.expected_config_revision,
                            receipt.ledger_revision,
                        )
                        try:
                            await self.__ledger.finalize_active(
                                claim, metadata_revision=metadata.revision
                            )
                        except SQLiteExecutorError:
                            raise
                        except Exception:
                            unresolved.append(self._recovery_transition(receipt))
                        continue

                    if referenced:
                        # The config points at this receipt, so deletion could
                        # destroy the only copy. Keep the persistent owner.
                        unresolved.append(self._recovery_transition(receipt))
                        continue

                    transition = self._recovery_transition(receipt)
                    try:
                        await self.__secret_store.recover(
                            transition, owner=transition.owner
                        )
                    except SQLiteExecutorError:
                        raise
                    except Exception:
                        unresolved.append(transition)

            return tuple(unresolved)

    def _recovery_transition(self, receipt: SecretReceipt) -> SecretTransition:
        owner = self._owner(receipt.target.field, receipt.operation_id)
        return SecretTransition(
            SecretCompensationState.RECOVERABLE,
            secret_ref=receipt.secret_ref,
            operation_id=receipt.operation_id,
            owner=owner,
            metadata_revision=receipt.expected_config_revision,
        )

    def _canonical_patch(self, patch: ConfigPatch) -> ConfigPatch:
        target = self.__target
        if not isinstance(patch, ConfigPatch):
            raise TypeError("patch must be a ConfigPatch")
        validate_config_patch(target, patch)
        declarations = {field.name: field for field in self.__fields}
        for update in patch.updates:
            declaration = declarations.get(update.field)
            if declaration is None:
                raise ValueError("config field is not declared by this module")
            if declaration.sensitive and update.mode is ConfigPatchMode.REPLACE:
                if update.secret is None:
                    raise ValueError(
                        "sensitive config replacement requires SecretMaterial"
                    )
            if not declaration.sensitive and update.secret is not None:
                raise ValueError("ordinary config field cannot receive a secret")
        operation_id = _bounded_operation_id(patch.operation_id)
        return ConfigPatch(
            patch.expected_revision,
            patch.updates,
            self.__fields,
            operation_id,
            target,
        )

    async def _old_refs(
        self, target: ConfigTarget, patch: ConfigPatch
    ) -> tuple[tuple[Any, Any], ...]:
        before = await self.__repository.current(target)
        if not isinstance(before, ConfigSnapshot):
            raise ValueError("configuration repository returned an invalid snapshot")
        if before.revision != patch.expected_revision:
            raise RevisionConflict("config", patch.expected_revision, before.revision)
        if before.target is not None and ConfigTarget.validate(before.target) != target:
            raise ValueError("configuration snapshot target does not match service")
        self._validate_snapshot_fields(before)
        old = {item.field: item for item in before.secret_metadata}
        result: list[tuple[Any, Any]] = []
        for update in patch.updates:
            if update.mode is ConfigPatchMode.KEEP:
                continue
            metadata = old.get(update.field)
            if metadata is not None and metadata.secret_ref is not None:
                result.append((metadata.secret_ref, update.field))
        return tuple(result)

    async def _recover_pending_operation(
        self, patch: ConfigPatch, *, excluded_tokens: set[str]
    ) -> tuple[object, ...]:
        """Reconcile a store that committed a stage before reporting failure."""

        failures: list[object] = []
        declarations = {item.name: item for item in self.__fields}
        for update in patch.updates:
            if (
                declarations[update.field].sensitive
                and update.mode is ConfigPatchMode.REPLACE
            ):
                target = SecretTarget(
                    self.__target.principal_id, self.__target.module_id, update.field
                )
                try:
                    pending = await self.__ledger.pending(target)
                except SQLiteExecutorError:
                    raise
                except Exception:
                    failures.append(target)
                    continue
                for receipt in pending:
                    try:
                        receipt = SecretReceipt.validate(receipt)
                    except (TypeError, ValueError):
                        failures.append(target)
                        continue
                    if (
                        receipt.secret_ref.token in excluded_tokens
                        or receipt.operation_id != patch.operation_id
                        or receipt.expected_config_revision != patch.expected_revision
                    ):
                        continue
                    failures.extend(
                        await self._compensate_claimed(
                            (receipt,), reason="stage_failed"
                        )
                    )
        return tuple(failures)

    def _validate_snapshot_fields(self, snapshot: ConfigSnapshot) -> None:
        declared = {field.name: field for field in self.__fields}
        if any(field not in declared for field in snapshot.values):
            raise ValueError("configuration snapshot contains an undeclared field")
        if any(declared[field].sensitive for field in snapshot.values):
            raise ValueError("configuration snapshot contains a sensitive value")
        for metadata in snapshot.secret_metadata:
            declaration = declared.get(metadata.field)
            if declaration is None or not declaration.sensitive:
                raise ValueError("configuration snapshot secret field is undeclared")

    async def _delete_old(
        self, refs: tuple[tuple[Any, Any], ...], patch: ConfigPatch
    ) -> tuple[object, ...]:
        failures: list[object] = []
        for ref, field in refs:
            owner = self._owner(field, ref.operation_id)
            try:
                await self.__secret_store.delete(ref, owner=owner)
            except SQLiteExecutorError:
                raise
            except Exception:
                try:
                    await self.__secret_store.mark_orphan(
                        ref, "config_secret_cleanup_failed", owner=owner
                    )
                except SQLiteExecutorError:
                    raise
                except Exception:
                    pass
                failures.append(
                    self._transition(
                        ref,
                        ref.operation_id,
                        SecretCompensationState.ORPHAN,
                        patch.expected_revision + 1,
                    )
                )
        return tuple(failures)

    async def _compensate_claimed(
        self, claims: Iterable[SecretReceipt], *, reason: str
    ) -> tuple[object, ...]:
        failures: list[object] = []
        for claim in claims:
            marked_ok = True
            try:
                marked = await self.__ledger.mark_cas_conflict(claim)
                marked = SecretReceipt.validate(marked)
            except SQLiteExecutorError:
                raise
            except Exception:
                marked = claim
                marked_ok = False
            owner = self._owner(marked.secret_ref.field, marked.operation_id)
            delete_failed = False
            try:
                await self.__secret_store.delete(marked.secret_ref, owner=owner)
            except SQLiteExecutorError:
                raise
            except Exception:
                delete_failed = True
                try:
                    await self.__secret_store.mark_orphan(
                        marked.secret_ref, reason, owner=owner
                    )
                except SQLiteExecutorError:
                    raise
                except Exception:
                    pass
                failures.append(
                    self._transition(
                        marked.secret_ref,
                        marked.operation_id,
                        SecretCompensationState.ORPHAN,
                        0,
                    )
                )
            if not delete_failed and not marked_ok:
                failures.append(
                    self._transition(
                        marked.secret_ref,
                        marked.operation_id,
                        SecretCompensationState.RECOVERABLE,
                        0,
                    )
                )
        return tuple(failures)

    async def _mark_recovery_claims(
        self, claims: Iterable[SecretReceipt], reason: str
    ) -> tuple[object, ...]:
        transitions: list[object] = []
        for claim in claims:
            transitions.append(
                self._transition(
                    claim.secret_ref,
                    claim.operation_id,
                    SecretCompensationState.RECOVERABLE,
                    0,
                )
            )
        return tuple(transitions)

    async def _compensate_unclaimed(
        self, receipts: Mapping[str, SecretReceipt], *, reason: str
    ) -> tuple[object, ...]:
        failures: list[object] = []
        for receipt in receipts.values():
            owner = self._owner(receipt.secret_ref.field, receipt.operation_id)
            try:
                await self.__secret_store.delete(receipt.secret_ref, owner=owner)
            except SQLiteExecutorError:
                raise
            except Exception:
                try:
                    await self.__secret_store.mark_orphan(
                        receipt.secret_ref, reason, owner=owner
                    )
                except SQLiteExecutorError:
                    raise
                except Exception:
                    pass
                failures.append(
                    self._transition(
                        receipt.secret_ref,
                        receipt.operation_id,
                        SecretCompensationState.ORPHAN,
                        receipt.expected_config_revision,
                    )
                )
        return tuple(failures)

    def _owner(self, field: str, operation_id: str) -> SecretOwner:
        return SecretOwner(
            self.__target.principal_id,
            self.__target.module_id,
            field,
            operation_id,
        )

    def _transition(
        self,
        secret_ref,
        operation_id: str,
        state: SecretCompensationState,
        metadata_revision: int,
    ) -> SecretTransition:
        owner = self._owner(secret_ref.field, operation_id)
        return SecretTransition(
            state,
            secret_ref=secret_ref,
            operation_id=operation_id,
            owner=owner,
            metadata_revision=max(0, metadata_revision),
        )

    def _transitions_for_old_refs(
        self, refs: tuple[tuple[Any, Any], ...], metadata_revision: int
    ) -> tuple[SecretTransition, ...]:
        return tuple(
            self._transition(
                ref,
                ref.operation_id,
                SecretCompensationState.RECOVERABLE,
                metadata_revision,
            )
            for ref, _field in refs
        )


class ConfigurationService:
    """Module-facing ConfigView facade for trusted same-process extensions.

    Its supported API is limited to ``current()``.  The host retains the
    coordinator and its update/clear capabilities; this declarative boundary
    does not claim to sandbox arbitrary same-process Python reflection.
    """

    __slots__ = ("__current",)

    def __init__(
        self,
        coordinator_or_target: _ConfigurationCoordinator | ConfigTarget,
        fields: Iterable[ConfigField] | None = None,
        repository: ConfigRepository | None = None,
        secret_store: SecretStore | None = None,
        ledger: SecretReceiptLedger | None = None,
    ) -> None:
        if isinstance(coordinator_or_target, _ConfigurationCoordinator):
            if any(
                value is not None
                for value in (fields, repository, secret_store, ledger)
            ):
                raise TypeError("coordinator facade does not accept storage arguments")
            coordinator = coordinator_or_target
        else:
            if fields is None or repository is None or secret_store is None:
                raise TypeError("configuration facade requires a coordinator")
            coordinator = _ConfigurationCoordinator(
                coordinator_or_target, fields, repository, secret_store, ledger
            )

        async def current() -> ConfigSnapshot:
            return await coordinator.current()

        object.__setattr__(self, "_ConfigurationService__current", current)

    async def current(self) -> ConfigSnapshot:
        current = object.__getattribute__(self, "_ConfigurationService__current")
        return await current()

    def __getattribute__(self, name: str):
        if name in {"current", "__class__", "__dir__"}:
            return object.__getattribute__(self, name)
        raise AttributeError("configuration facade exposes only current")

    def __dir__(self) -> list[str]:
        return ["current"]


ConfigurationCoordinator = _ConfigurationCoordinator
ConfigViewService = ConfigurationService
ConfigCoordinator = _ConfigurationCoordinator
ModuleConfiguration = ConfigurationService
ConfigService = ConfigurationService
ScopedConfigView = ConfigurationService

__all__ = [
    "ConfigCoordinator",
    "ConfigService",
    "ConfigurationCoordinator",
    "ConfigViewService",
    "ConfigurationRecoveryRequired",
    "ConfigurationService",
    "ConfigurationUpdateFailed",
    "ModuleConfiguration",
    "ScopedConfigView",
]
