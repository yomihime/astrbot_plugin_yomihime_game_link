"""Controlled secret payload storage and the persistent receipt ledger.

No plaintext fallback exists.  A caller must provide a codec with ``encrypt``
and ``decrypt`` methods; without one the store remains deliberately unavailable.
The codec is an infrastructure boundary so the product can select and review a
key provider and algorithm separately from this repository task.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Protocol

from ..api.storage import (
    ClaimedSecretReceipt,
    SecretReceipt,
    SecretReceiptState,
    SecretRef,
    SecretTarget,
)
from ..core.ports import (
    RevisionConflict,
    SecretCompensationState,
    SecretOwner,
    SecretTransition,
)
from .sqlite.database import SQLiteDatabase, SQLiteUnitOfWork
from .sqlite.repositories_auth import _database, _ref


class SecretCodec(Protocol):
    def encrypt(self, value: bytes) -> bytes: ...

    def decrypt(self, value: bytes) -> bytes: ...


class SecretStoreUnavailable(RuntimeError):
    code = "secret_store_unavailable"

    def __init__(self) -> None:
        super().__init__("secret storage is unavailable")


class SQLiteSecretStore:
    """Filesystem payload store plus SQLite-backed issuance ledger."""

    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        root: str | Path,
        *,
        codec: SecretCodec | None = None,
    ) -> None:
        self.database = _database(database)
        self.root = Path(root).expanduser()
        self.codec = codec

    def _require_codec(self) -> SecretCodec:
        codec = self.codec
        if (
            codec is None
            or not callable(getattr(codec, "encrypt", None))
            or not callable(getattr(codec, "decrypt", None))
        ):
            raise SecretStoreUnavailable()
        return codec

    def encryption_readiness(self) -> dict[str, str | bool]:
        """Probe only the current injected codec in memory, never stored secrets."""
        try:
            codec = self._require_codec()
            probe = b"yomihime:encryption-readiness:v1"
            encrypted = codec.encrypt(probe)
            if type(encrypted) is not bytes or encrypted == probe:
                raise ValueError
            if codec.decrypt(encrypted) != probe:
                raise ValueError
        except Exception:
            return {
                "ready": False,
                "state": "unavailable",
                "reason_code": "secret_encryption_unavailable",
            }
        return {"ready": True, "state": "ready", "reason_code": "ready"}

    @staticmethod
    def _target(target: SecretTarget) -> SecretTarget:
        if not isinstance(target, SecretTarget):
            raise TypeError("secret target is required")
        return SecretTarget(target.principal_id, target.module_id, target.field)

    def _path(self, token: str) -> Path:
        # The token was generated locally and is revalidated before use.
        if not token.startswith("secret_") or any(c in token for c in "/\\\r\n"):
            raise ValueError("invalid secret reference")
        return self.root / f"{token}.blob"

    def _write_payload(self, token: str, value: bytes) -> None:
        if not isinstance(value, bytes):
            raise TypeError("secret payload must be bytes")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise SecretStoreUnavailable() from None
        codec = self._require_codec()
        try:
            encrypted = codec.encrypt(value)
        except Exception:
            raise SecretStoreUnavailable() from None
        if not isinstance(encrypted, bytes) or encrypted == value:
            raise SecretStoreUnavailable()
        path = self._path(token)
        temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
        try:
            with open(temporary, "xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, ValueError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise SecretStoreUnavailable() from None

    async def stage(
        self,
        value: bytes,
        *,
        target: SecretTarget,
        operation_id: str,
        expected_config_revision: int,
    ) -> SecretReceipt:
        target = self._target(target)
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be bounded")
        if (
            isinstance(expected_config_revision, bool)
            or not isinstance(expected_config_revision, int)
            or expected_config_revision < 0
        ):
            raise ValueError("expected config revision must be non-negative")
        token = f"secret_{secrets.token_urlsafe(24)}"
        ref = SecretRef(
            token, target.principal_id, target.module_id, target.field, operation_id
        )

        def reserve(unit: SQLiteUnitOfWork) -> int:
            row = unit.execute(
                "SELECT COALESCE(MAX(ledger_revision), 0) AS revision FROM secret_receipts"
            ).fetchone()
            revision = int(row["revision"]) + 1
            unit.execute(
                "INSERT INTO secret_receipts(secret_token,principal_id,module_id,field,operation_id,expected_config_revision,ledger_revision,state,payload_name) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    token,
                    target.principal_id,
                    target.module_id,
                    target.field,
                    operation_id,
                    expected_config_revision,
                    revision,
                    SecretReceiptState.RECOVERABLE.value,
                    f"{token}.blob",
                ),
            )

            return revision

        # Before this transaction commits there is no payload to compensate.
        # If cancellation occurs while waiting for capacity, propagate it
        # without queueing cleanup; accepted work is drained by the executor.
        reserved_revision = await self.database.executor.run_transaction(
            reserve, begin_mode="IMMEDIATE"
        )
        self._write_payload(token, value)

        def finalize(unit: SQLiteUnitOfWork) -> SecretReceipt:
            cursor = unit.execute(
                "UPDATE secret_receipts SET state='staged', ledger_revision=ledger_revision+1 "
                "WHERE secret_token=? AND principal_id=? AND module_id=? AND field=? "
                "AND operation_id=? AND expected_config_revision=? "
                "AND ledger_revision=? AND state='recoverable'",
                (
                    token,
                    target.principal_id,
                    target.module_id,
                    target.field,
                    operation_id,
                    expected_config_revision,
                    reserved_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "secret_receipt", reserved_revision, reserved_revision
                )
            return SecretReceipt(
                ref,
                target,
                operation_id,
                expected_config_revision,
                reserved_revision + 1,
            )

        # A later cancellation/failure leaves the durable receipt as owner.
        # Finalize is either rolled back (RECOVERABLE) or committed (STAGED),
        # both of which remain visible to pending/restart recovery. No extra
        # executor job is queued after the caller's cancellation.
        return await self.database.executor.run_transaction(
            finalize, begin_mode="IMMEDIATE"
        )

    async def claim_for_config(
        self,
        receipt: SecretReceipt,
        *,
        target: SecretTarget,
        operation_id: str,
        expected_config_revision: int,
        expected_ledger_revision: int,
    ) -> ClaimedSecretReceipt:
        receipt = SecretReceipt.validate(receipt)
        target = self._target(target)
        if (
            receipt.target != target
            or receipt.operation_id != operation_id
            or receipt.expected_config_revision != expected_config_revision
        ):
            raise ValueError("secret receipt does not match claim")

        def claim(unit: SQLiteUnitOfWork) -> ClaimedSecretReceipt:
            row = unit.execute(
                "SELECT * FROM secret_receipts WHERE secret_token=?",
                (receipt.secret_ref.token,),
            ).fetchone()
            if row is None:
                raise RevisionConflict("secret_receipt", expected_ledger_revision, 0)
            if (
                int(row["ledger_revision"]) != expected_ledger_revision
                or row["state"] != SecretReceiptState.STAGED.value
                or row["principal_id"] != target.principal_id
                or row["module_id"] != target.module_id
                or row["field"] != target.field
                or row["operation_id"] != operation_id
                or int(row["expected_config_revision"]) != expected_config_revision
            ):
                raise RevisionConflict(
                    "secret_receipt",
                    expected_ledger_revision,
                    int(row["ledger_revision"]),
                )
            cursor = unit.execute(
                "UPDATE secret_receipts SET state='claimed', ledger_revision=ledger_revision+1 WHERE secret_token=? AND ledger_revision=? AND state='staged'",
                (receipt.secret_ref.token, expected_ledger_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "secret_receipt",
                    expected_ledger_revision,
                    int(row["ledger_revision"]),
                )
            return ClaimedSecretReceipt(
                receipt.secret_ref,
                target,
                operation_id,
                expected_config_revision,
                expected_ledger_revision + 1,
            )

        return await self.database.executor.run_transaction(
            claim, begin_mode="IMMEDIATE"
        )

    async def finalize_active(
        self, receipt: ClaimedSecretReceipt, *, metadata_revision: int
    ) -> SecretReceipt:
        receipt = ClaimedSecretReceipt.validate(receipt)
        if (
            isinstance(metadata_revision, bool)
            or not isinstance(metadata_revision, int)
            or metadata_revision < 1
        ):
            raise ValueError("metadata revision must be positive")

        def finalize(unit: SQLiteUnitOfWork) -> SecretReceipt:
            row = unit.execute(
                "SELECT * FROM secret_receipts WHERE secret_token=?",
                (receipt.secret_ref.token,),
            ).fetchone()
            if row is None:
                raise RevisionConflict("secret_receipt", receipt.ledger_revision, 0)
            authoritative = _ref(row)
            if (
                row["state"] != SecretReceiptState.CLAIMED.value
                or int(row["ledger_revision"]) != receipt.ledger_revision
                or authoritative != receipt.secret_ref
                or row["principal_id"] != receipt.target.principal_id
                or row["module_id"] != receipt.target.module_id
                or row["field"] != receipt.target.field
                or row["operation_id"] != receipt.operation_id
                or int(row["expected_config_revision"])
                != receipt.expected_config_revision
            ):
                raise RevisionConflict(
                    "secret_receipt",
                    receipt.ledger_revision,
                    int(row["ledger_revision"]),
                )
            cursor = unit.execute(
                "UPDATE secret_receipts SET state='active', metadata_revision=?, "
                "ledger_revision=ledger_revision+1 WHERE secret_token=? "
                "AND principal_id=? AND module_id=? AND field=? AND operation_id=? "
                "AND expected_config_revision=? AND ledger_revision=? AND state='claimed'",
                (
                    metadata_revision,
                    receipt.secret_ref.token,
                    receipt.target.principal_id,
                    receipt.target.module_id,
                    receipt.target.field,
                    receipt.operation_id,
                    receipt.expected_config_revision,
                    receipt.ledger_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "secret_receipt", receipt.ledger_revision, receipt.ledger_revision
                )
            return SecretReceipt(
                receipt.secret_ref,
                receipt.target,
                receipt.operation_id,
                receipt.expected_config_revision,
                receipt.ledger_revision + 1,
                SecretReceiptState.ACTIVE,
            )

        return await self.database.executor.run_transaction(
            finalize, begin_mode="IMMEDIATE"
        )

    async def mark_cas_conflict(self, receipt: SecretReceipt) -> SecretReceipt:
        receipt = SecretReceipt.validate(receipt)

        def mark(unit: SQLiteUnitOfWork) -> SecretReceipt:
            row = unit.execute(
                "SELECT * FROM secret_receipts WHERE secret_token=?",
                (receipt.secret_ref.token,),
            ).fetchone()
            if row is None:
                raise RevisionConflict("secret_receipt", receipt.ledger_revision, 0)
            authoritative = _ref(row)
            if (
                authoritative != receipt.secret_ref
                or row["principal_id"] != receipt.target.principal_id
                or row["module_id"] != receipt.target.module_id
                or row["field"] != receipt.target.field
                or row["operation_id"] != receipt.operation_id
                or int(row["expected_config_revision"])
                != receipt.expected_config_revision
                or int(row["ledger_revision"]) != receipt.ledger_revision
                or row["state"] not in {"claimed", "staged", "recoverable"}
            ):
                raise RevisionConflict(
                    "secret_receipt",
                    receipt.ledger_revision,
                    int(row["ledger_revision"]),
                )
            cursor = unit.execute(
                "UPDATE secret_receipts SET state='cas_conflict', ledger_revision=ledger_revision+1 "
                "WHERE secret_token=? AND principal_id=? AND module_id=? AND field=? "
                "AND operation_id=? AND expected_config_revision=? AND ledger_revision=? "
                "AND state IN ('claimed','staged','recoverable')",
                (
                    receipt.secret_ref.token,
                    receipt.target.principal_id,
                    receipt.target.module_id,
                    receipt.target.field,
                    receipt.operation_id,
                    receipt.expected_config_revision,
                    receipt.ledger_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "secret_receipt", receipt.ledger_revision, receipt.ledger_revision
                )
            return SecretReceipt(
                receipt.secret_ref,
                receipt.target,
                receipt.operation_id,
                receipt.expected_config_revision,
                receipt.ledger_revision + 1,
                SecretReceiptState.CAS_CONFLICT,
            )

        return await self.database.executor.run_transaction(
            mark, begin_mode="IMMEDIATE"
        )

    async def pending(
        self, target: SecretTarget | SecretOwner
    ) -> tuple[SecretReceipt, ...] | tuple[SecretTransition, ...]:
        if isinstance(target, SecretOwner):
            owner = SecretOwner(
                target.principal_id,
                target.module_id,
                target.field,
                target.operation_id,
            )

            def read_transitions(
                unit: SQLiteUnitOfWork,
            ) -> tuple[SecretTransition, ...]:
                rows = unit.execute(
                    "SELECT * FROM secret_receipts WHERE principal_id=? AND module_id=? "
                    "AND field=? AND operation_id=? AND state IN "
                    "('staged','claimed','orphan','delete_failed','recoverable') "
                    "ORDER BY ledger_revision",
                    (
                        owner.principal_id,
                        owner.module_id,
                        owner.field,
                        owner.operation_id,
                    ),
                ).fetchall()
                return tuple(
                    SecretTransition(
                        SecretCompensationState(
                            "staged" if row["state"] == "claimed" else row["state"]
                        ),
                        secret_ref=_ref(row),
                        operation_id=owner.operation_id,
                        owner=owner,
                        metadata_revision=int(row["metadata_revision"]),
                    )
                    for row in rows
                )

            return await self.database.executor.run_read(read_transitions)
        target = self._target(target)

        def read_receipts(unit: SQLiteUnitOfWork) -> tuple[SecretReceipt, ...]:
            rows = unit.execute(
                "SELECT * FROM secret_receipts WHERE principal_id=? AND module_id=? AND field=? AND state IN ('staged','claimed','orphan','delete_failed','recoverable') ORDER BY ledger_revision",
                (target.principal_id, target.module_id, target.field),
            ).fetchall()
            return tuple(
                SecretReceipt(
                    _ref(row),
                    target,
                    row["operation_id"],
                    int(row["expected_config_revision"]),
                    int(row["ledger_revision"]),
                    SecretReceiptState(row["state"]),
                )
                for row in rows
            )

        return await self.database.executor.run_read(read_receipts)

    async def put(self, value: bytes, *, owner: SecretOwner) -> SecretReceipt:
        owner = SecretOwner(
            owner.principal_id, owner.module_id, owner.field, owner.operation_id
        )
        return await self.stage(
            value,
            target=SecretTarget(owner.principal_id, owner.module_id, owner.field),
            operation_id=owner.operation_id,
            expected_config_revision=0,
        )

    async def _owned_row(
        self, secret_ref: SecretRef, owner: SecretOwner
    ) -> tuple[SecretRef, str | None]:
        ref = SecretRef.validate(secret_ref)
        owner = SecretOwner(
            owner.principal_id, owner.module_id, owner.field, owner.operation_id
        )

        def read(unit: SQLiteUnitOfWork) -> tuple[SecretRef, str | None]:
            row = unit.execute(
                "SELECT * FROM secret_receipts WHERE secret_token=?", (ref.token,)
            ).fetchone()
            if row is None:
                return ref, None
            # The persisted ledger row is authoritative.  The ref is a
            # copyable DTO, so its owner fields cannot authorize access.
            authoritative = _ref(row)
            if ref != authoritative or (
                row["principal_id"],
                row["module_id"],
                row["field"],
                row["operation_id"],
            ) != (
                owner.principal_id,
                owner.module_id,
                owner.field,
                owner.operation_id,
            ):
                raise ValueError("secret reference owner mismatch")
            return authoritative, str(row["state"])

        return await self.database.executor.run_read(read)

    async def read(self, secret_ref: SecretRef, *, owner: SecretOwner) -> bytes | None:
        codec = self._require_codec()
        ref, state = await self._owned_row(secret_ref, owner)
        if state not in {"staged", "claimed", "active"}:
            return None
        path = self._path(ref.token)
        try:
            encoded = path.read_bytes()
            value = codec.decrypt(encoded)
            return value if isinstance(value, bytes) else None
        except (OSError, ValueError, TypeError):
            raise SecretStoreUnavailable() from None

    async def delete(self, secret_ref: SecretRef, *, owner: SecretOwner) -> None:
        ref, state = await self._owned_row(secret_ref, owner)
        if state is None:
            return
        path = self._path(ref.token)
        try:
            path.unlink(missing_ok=True)
        except OSError:

            def mark_delete_failed(unit: SQLiteUnitOfWork) -> None:
                unit.execute(
                    "UPDATE secret_receipts SET state='delete_failed', ledger_revision=ledger_revision+1 WHERE secret_token=?",
                    (ref.token,),
                )

            await self.database.executor.run_transaction(
                mark_delete_failed, begin_mode="IMMEDIATE"
            )
            raise SecretStoreUnavailable() from None

        def mark_deleted(unit: SQLiteUnitOfWork) -> None:
            unit.execute(
                "UPDATE secret_receipts SET state='tombstoned', ledger_revision=ledger_revision+1 WHERE secret_token=?",
                (ref.token,),
            )

        await self.database.executor.run_transaction(
            mark_deleted, begin_mode="IMMEDIATE"
        )

    async def mark_orphan(
        self, secret_ref: SecretRef, reason: str, *, owner: SecretOwner
    ) -> None:
        ref, state = await self._owned_row(secret_ref, owner)
        if state is None:
            return

        def mark(unit: SQLiteUnitOfWork) -> None:
            unit.execute(
                "UPDATE secret_receipts SET state='orphan', ledger_revision=ledger_revision+1 WHERE secret_token=?",
                (ref.token,),
            )

        await self.database.executor.run_transaction(mark, begin_mode="IMMEDIATE")

    async def recover(
        self, transition: SecretTransition, *, owner: SecretOwner
    ) -> SecretTransition:
        ref = transition.secret_ref or transition.old_secret_ref
        if ref is None:
            raise ValueError("secret transition has no reference")
        await self.delete(ref, owner=owner)
        return SecretTransition(
            SecretCompensationState.CLEANED,
            secret_ref=ref,
            operation_id=owner.operation_id,
            owner=owner,
            metadata_revision=transition.metadata_revision,
            retry_count=transition.retry_count + 1,
        )


SQLiteSecretReceiptLedger = SQLiteSecretStore
SecretStore = SQLiteSecretStore
SecretReceiptLedger = SQLiteSecretStore

__all__ = [
    "SecretCodec",
    "SecretReceiptLedger",
    "SecretStore",
    "SecretStoreUnavailable",
    "SQLiteSecretReceiptLedger",
    "SQLiteSecretStore",
]
