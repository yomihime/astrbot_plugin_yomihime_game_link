"""Durable, fail-closed selection of the Core secret-store master key.

Only a proven-empty store can create a key without operator input.  The
SQLite operation journal is committed before a private candidate file is
created; activation and every credential-reference change are serialized by
SQLite's cross-process IMMEDIATE writer lock.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import stat
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .key_provider import EnvironmentKeyProvider, SecretKeyUnavailable
from .managed_key_file import ManagedKeyFileError, ManagedKeyFiles
from .secret_codec import _MAGIC, _NONCE_BYTES, _TAG_BYTES, AESGCMSecretCodec
from .sqlite.database import SQLiteDatabase, SQLiteUnitOfWork

_CHECK_CONTEXT = b"yomihime:managed-master-key:check:v1"
_RESET_CONFIRMATION = "RESET ENCRYPTED CREDENTIALS"
_LIVE_STATES = frozenset(
    {
        "staged",
        "claimed",
        "active",
        "orphan",
        "delete_failed",
        "recoverable",
        "cas_conflict",
    }
)


class ManagedKeyConflict(RuntimeError):
    """A bounded management failure with no input or key material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _check(key: bytes) -> bytes:
    return hmac.new(key, _CHECK_CONTEXT, hashlib.sha256).digest()


def _decode_key(encoded: str) -> bytes:
    if type(encoded) is not str or len(encoded) > 128:
        raise ManagedKeyConflict("invalid_key")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ManagedKeyConflict("invalid_key") from None
    if len(key) != 32:
        raise ManagedKeyConflict("invalid_key")
    return key


class ManagedKeyManager:
    """Private Core key provider and management coordinator."""

    def __init__(
        self,
        database: SQLiteDatabase,
        data_root: str | Path,
        secret_root: str | Path,
        *,
        environment: EnvironmentKeyProvider | None = None,
        files: ManagedKeyFiles | None = None,
    ) -> None:
        self.database = database
        self.data_root = Path(data_root)
        self.secret_root = Path(secret_root)
        self.environment = environment or EnvironmentKeyProvider("YGL_SECRET_KEY")
        self.files = files or ManagedKeyFiles(self.data_root)
        self._generation: int | None = None
        self._revision: int | None = None

    @staticmethod
    def _selection(unit: SQLiteUnitOfWork):
        row = unit.execute(
            "SELECT * FROM managed_master_key WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise ManagedKeyConflict("selection_unavailable")
        return row

    @staticmethod
    def _pending(unit: SQLiteUnitOfWork):
        return unit.execute(
            "SELECT * FROM managed_master_key_operations WHERE state='preparing'"
        ).fetchone()

    def _environment_key(self) -> tuple[bool, bytes | None, str | None]:
        # Membership matters: an explicitly empty variable must never fall
        # through to a managed file or trigger fresh initialization.
        if "YGL_SECRET_KEY" not in os.environ:
            return False, None, None
        try:
            return True, self.environment.get_key(), None
        except SecretKeyUnavailable as error:
            return True, None, error.reason
        except Exception:
            return True, None, "cannot_safely_confirm"

    @staticmethod
    def _file_names(root: Path) -> tuple[str, ...]:
        try:
            info = root.lstat()
        except FileNotFoundError:
            return ()
        except OSError:
            raise ManagedKeyConflict("inventory_unknown") from None
        if not stat.S_ISDIR(info.st_mode):
            raise ManagedKeyConflict("inventory_unknown")
        try:
            with os.scandir(root) as entries:
                names = []
                for entry in entries:
                    if not entry.is_file(follow_symlinks=False):
                        raise ManagedKeyConflict("inventory_unknown")
                    names.append(entry.name)
                return tuple(sorted(names))
        except OSError:
            raise ManagedKeyConflict("inventory_unknown") from None

    def _inventory(self, unit: SQLiteUnitOfWork) -> dict:
        receipts = unit.execute(
            "SELECT secret_token,principal_id,module_id,field,operation_id,state,"
            "payload_name,key_generation "
            "FROM secret_receipts ORDER BY secret_token"
        ).fetchall()
        configs = unit.execute(
            "SELECT principal_id,module_id,field,secret_token,secret_principal_id,"
            "secret_module_id,secret_field,secret_operation_id FROM config_entries "
            "WHERE secret_token IS NOT NULL ORDER BY principal_id,module_id,field"
        ).fetchall()
        grants = unit.execute(
            "SELECT grant_id,revision,status,principal_id,module_id,secret_token,secret_principal_id,"
            "secret_module_id,secret_field,secret_operation_id FROM grants "
            "WHERE secret_token IS NOT NULL ORDER BY grant_id"
        ).fetchall()
        selection = self._selection(unit)
        generation = int(selection["generation"])
        reset_seen = (
            unit.execute(
                "SELECT 1 FROM managed_master_key_operations WHERE kind='reset' "
                "AND state='committed' LIMIT 1"
            ).fetchone()
            is not None
        )
        names = self._file_names(self.secret_root)
        try:
            key_names = self.files.entries()
        except ManagedKeyFileError:
            raise ManagedKeyConflict("inventory_unknown") from None
        receipt_by_name = {}
        receipt_by_token = {}
        live_names = []
        unknown = 0
        impacts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        facts: list[str] = [f"g:{generation}", f"s:{selection['source']}"]
        for row in receipts:
            token, name = str(row["secret_token"]), str(row["payload_name"])
            key_generation = int(row["key_generation"])
            state = str(row["state"])
            if name != f"{token}.blob" or name in receipt_by_name:
                unknown += 1
            receipt_by_name[name] = row
            receipt_by_token[token] = row
            retired = reset_seen and key_generation < generation
            if retired and state in _LIVE_STATES:
                unknown += 1
            if state in _LIVE_STATES and not retired:
                live_names.append(name)
            facts.append(f"r:{token}:{state}:{name}:{key_generation}")
        disk = set(names)
        for name in names:
            if name not in receipt_by_name:
                unknown += 1
            facts.append(f"f:{name}")
        for name in live_names:
            if name not in disk:
                unknown += 1

        def valid_reference(row) -> bool:
            receipt = receipt_by_token.get(str(row["secret_token"]))
            if receipt is None or receipt["state"] != "active":
                return False
            if int(receipt["key_generation"]) != generation:
                return False
            if str(receipt["payload_name"]) not in disk:
                return False
            return (
                receipt["principal_id"],
                receipt["module_id"],
                receipt["field"],
                receipt["operation_id"],
            ) == (
                row["secret_principal_id"],
                row["secret_module_id"],
                row["secret_field"],
                row["secret_operation_id"],
            )

        for row in configs:
            token = str(row["secret_token"])
            if not valid_reference(row) or (
                row["principal_id"],
                row["module_id"],
                row["field"],
            ) != (
                row["secret_principal_id"],
                row["secret_module_id"],
                row["secret_field"],
            ):
                unknown += 1
            impacts[str(row["module_id"])][0] += 1
            facts.append(
                f"c:{row['principal_id']}:{row['module_id']}:{row['field']}:{token}"
            )
        for row in grants:
            token = str(row["secret_token"])
            if not valid_reference(row) or (row["principal_id"], row["module_id"]) != (
                row["secret_principal_id"],
                row["secret_module_id"],
            ):
                unknown += 1
            impacts[str(row["module_id"])][1] += 1
            facts.append(
                f"a:{row['grant_id']}:{row['revision']}:{row['status']}:"
                f"{row['module_id']}:{token}"
            )
        expected_key = (
            f"master-{selection['key_id']}.key"
            if selection["source"] == "managed"
            else None
        )
        pending = self._pending(unit)
        pending_key = (
            f"master-{pending['candidate_key_id']}.key"
            if pending is not None and pending["candidate_key_id"]
            else None
        )
        abandoned_keys = {
            f"master-{row['candidate_key_id']}.key"
            for row in unit.execute(
                "SELECT candidate_key_id FROM managed_master_key_operations "
                "WHERE state='abandoned' AND candidate_key_id IS NOT NULL"
            ).fetchall()
        }
        for name in key_names:
            if name not in {expected_key, pending_key} | abandoned_keys:
                # Committed reset generations may retain an inactive key file.
                retired_key = unit.execute(
                    "SELECT 1 FROM managed_master_key_retired WHERE key_id=? LIMIT 1",
                    (name.removeprefix("master-").removesuffix(".key"),),
                ).fetchone()
                if retired_key is None:
                    unknown += 1
            if name != pending_key:
                facts.append(f"k:{name}")
        digest = hashlib.sha256("\n".join(facts).encode("utf-8")).hexdigest()
        return {
            "digest": digest,
            "unknown": unknown,
            "live": tuple(live_names),
            "impact": tuple(
                {
                    "module_id": module,
                    "config_count": counts[0],
                    "grant_count": counts[1],
                }
                for module, counts in sorted(impacts.items())
            ),
            "names": disk,
            "key_names": set(key_names),
        }

    def _decrypt_all(self, key: bytes, inventory: dict) -> None:
        try:
            cipher = AESGCM(key)
        except Exception:
            raise ManagedKeyConflict("encryption_component_unavailable") from None
        for name in inventory["live"]:
            if name not in inventory["names"]:
                raise ManagedKeyConflict("inventory_unknown")
            path = self.secret_root / name
            try:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ManagedKeyConflict("inventory_unknown")
                raw = path.read_bytes()
                after = path.lstat()
                if (before.st_dev, before.st_ino, before.st_size) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                ):
                    raise ManagedKeyConflict("inventory_unknown")
            except ManagedKeyConflict:
                raise
            except Exception:
                raise ManagedKeyConflict("inventory_unknown") from None
            if (
                type(raw) is not bytes
                or not raw.startswith(_MAGIC)
                or len(raw) < len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES
            ):
                raise ManagedKeyConflict("inventory_unknown")
            try:
                AESGCMSecretCodec._decrypt_payload(raw, cipher)
            except InvalidTag:
                raise ManagedKeyConflict("key_does_not_decrypt_inventory") from None
            except Exception:
                raise ManagedKeyConflict("encryption_component_unavailable") from None

    def _status_in(self, unit: SQLiteUnitOfWork) -> dict:
        row = self._selection(unit)
        inventory = self._inventory(unit)
        present, env_key, env_error = self._environment_key()
        pending = self._pending(unit)
        state, reason = "needs_restore", "key_missing"
        selected_key: bytes | None = None
        if inventory["unknown"]:
            state, reason = "unknown", "inventory_unknown"
        elif env_error is not None:
            state, reason = "initialization_failed", "environment_key_invalid"
        elif pending is not None:
            state, reason = "initialization_failed", "operation_incomplete"
        elif row["source"] == "unselected":
            state, reason = "needs_restore", "key_unselected"
        elif row["source"] == "environment":
            if not present:
                state, reason = "needs_restore", "environment_key_missing"
            elif env_key is not None and hmac.compare_digest(
                _check(env_key), row["key_check"]
            ):
                state, reason = "ready", "ready"
                selected_key = env_key
            else:
                state, reason = "source_conflict", "environment_key_conflict"
        else:
            expected_name = f"master-{row['key_id']}.key"
            try:
                key = self.files.read(str(row["key_id"]))
            except ManagedKeyFileError:
                key = None
            if key is None:
                state, reason = (
                    ("initialization_failed", "managed_key_unreadable")
                    if expected_name in inventory["key_names"]
                    else ("needs_restore", "managed_key_missing")
                )
            elif not hmac.compare_digest(_check(key), row["key_check"]):
                state, reason = "source_conflict", "managed_key_conflict"
            elif (
                present
                and env_key is not None
                and not hmac.compare_digest(_check(env_key), row["key_check"])
            ):
                state, reason = "source_conflict", "environment_key_conflict"
            else:
                state, reason = "ready", "ready"
                selected_key = key
        if state == "ready" and selected_key is not None:
            try:
                self._decrypt_all(selected_key, inventory)
            except ManagedKeyConflict as error:
                if error.code == "key_does_not_decrypt_inventory":
                    state, reason = "needs_restore", "inventory_decryption_failed"
                elif error.code == "inventory_unknown":
                    state, reason = "unknown", "inventory_unknown"
                    inventory["unknown"] += 1
                else:
                    state, reason = "initialization_failed", error.code
        return {
            "state": state,
            "reason_code": reason,
            "ready": state == "ready",
            "source": str(row["source"]),
            "generation": int(row["generation"]),
            "revision": int(row["revision"]),
            "inventory_digest": inventory["digest"],
            "unknown_count": inventory["unknown"],
            "impact": inventory["impact"],
        }

    async def status(self) -> dict:
        return await self.database.executor.run_read(self._status_in)

    def get_key(self) -> bytes:
        """Read current authority on every use so other processes cannot keep an old key."""
        if self._generation is None or self._revision is None:
            raise SecretKeyUnavailable()
        try:
            connection = self.database.connect()
            try:
                row = connection.execute(
                    "SELECT source,generation,revision,key_id,key_check "
                    "FROM managed_master_key WHERE singleton=1"
                ).fetchone()
                pending = connection.execute(
                    "SELECT 1 FROM managed_master_key_operations "
                    "WHERE state='preparing' LIMIT 1"
                ).fetchone()
            finally:
                connection.close()
            if (
                pending is not None
                or row is None
                or (row["generation"], row["revision"])
                != (
                    self._generation,
                    self._revision,
                )
            ):
                raise SecretKeyUnavailable()
            present, env_key, env_error = self._environment_key()
            if env_error is not None:
                raise SecretKeyUnavailable(reason=env_error)
            if row["source"] == "environment":
                if not present or env_key is None:
                    raise SecretKeyUnavailable(reason="not_provided")
                key = env_key
            elif row["source"] == "managed":
                key = self.files.read(str(row["key_id"]))
                if (
                    present
                    and env_key is not None
                    and not hmac.compare_digest(_check(env_key), row["key_check"])
                ):
                    raise SecretKeyUnavailable()
            else:
                raise SecretKeyUnavailable()
            if not hmac.compare_digest(_check(key), row["key_check"]):
                raise SecretKeyUnavailable()
            return key
        except SecretKeyUnavailable:
            raise
        except Exception:
            raise SecretKeyUnavailable() from None

    @staticmethod
    def _expect(status: dict, revision: int, generation: int, digest: str) -> None:
        if (revision, generation, digest) != (
            status["revision"],
            status["generation"],
            status["inventory_digest"],
        ):
            raise ManagedKeyConflict("stale_inventory")

    def _rotate_abandoned_candidate(
        self, unit: SQLiteUnitOfWork, pending, inventory: dict
    ):
        """Keep a pre-commit file, then journal a fresh never-overwritten name.

        Only a file that passed the private path/handle checks may be retired.
        An ACL, identity, or I/O failure remains unknown rather than becoming
        permission to create another key.
        """
        key_id = str(pending["candidate_key_id"])
        if f"master-{key_id}.key" not in inventory["key_names"]:
            return pending, inventory
        try:
            self.files.read(key_id)
        except ManagedKeyFileError as error:
            if error.code != "key_file_invalid":
                raise ManagedKeyConflict("inventory_unknown") from None
        else:
            # A complete, privately verified file is the durable candidate
            # whose selector commit was interrupted. Reuse its exact bytes.
            return pending, inventory
        unit.execute(
            "UPDATE managed_master_key_operations SET state='abandoned' "
            "WHERE operation_id=? AND state='preparing'",
            (pending["operation_id"],),
        )
        refreshed = self._inventory(unit)
        if refreshed["unknown"]:
            raise ManagedKeyConflict("inventory_unknown")
        operation_id, new_key_id = secrets.token_hex(16), secrets.token_hex(16)
        unit.execute(
            "INSERT INTO managed_master_key_operations "
            "(operation_id,kind,state,candidate_key_id,expected_generation,"
            "expected_revision,inventory_digest) VALUES(?,?,'preparing',?,?,?,?)",
            (
                operation_id,
                pending["kind"],
                new_key_id,
                pending["expected_generation"],
                pending["expected_revision"],
                refreshed["digest"],
            ),
        )
        return self._pending(unit), refreshed

    async def _prepare_candidate(
        self,
        operation_id: str,
        *,
        fence: Callable[[SQLiteUnitOfWork], None] | None = None,
        allow_completed: bool = False,
    ) -> str:
        def rotate(unit: SQLiteUnitOfWork) -> str:
            if fence is not None:
                fence(unit)
            pending = self._pending(unit)
            if (
                pending is None
                and allow_completed
                and self._selection(unit)["source"] == "managed"
            ):
                return ""
            if pending is None or pending["operation_id"] != operation_id:
                raise ManagedKeyConflict("operation_changed")
            row, inventory = self._selection(unit), self._inventory(unit)
            if (row["generation"], row["revision"], inventory["digest"]) != (
                pending["expected_generation"],
                pending["expected_revision"],
                pending["inventory_digest"],
            ) or inventory["unknown"]:
                raise ManagedKeyConflict("stale_inventory")
            candidate, _inventory = self._rotate_abandoned_candidate(
                unit, pending, inventory
            )
            return str(candidate["operation_id"])

        # Commit an abandoned candidate before attempting another CREATE_NEW.
        # A failed write can then only leave a file owned by the new journal row.
        return await self.database.executor.run_transaction(
            rotate, begin_mode="IMMEDIATE"
        )

    async def startup(self) -> dict:
        """Select a source or initialize only if every inventory proves empty."""

        def prepare(unit: SQLiteUnitOfWork):
            status = self._status_in(unit)
            inventory = self._inventory(unit)
            row = self._selection(unit)
            present, env_key, env_error = self._environment_key()
            if status["ready"]:
                return "ready", status
            if inventory["unknown"] or env_error is not None:
                return "blocked", status
            pending = self._pending(unit)
            if pending is not None:
                if (
                    pending["kind"] == "initialize"
                    and row["source"] == "unselected"
                    and not present
                    and not inventory["live"]
                    and not inventory["names"]
                    and inventory["digest"] == pending["inventory_digest"]
                ):
                    return "create", str(pending["operation_id"])
                return "blocked", status
            if row["source"] != "unselected":
                return "blocked", status
            if present:
                assert env_key is not None
                try:
                    self._decrypt_all(env_key, inventory)
                except ManagedKeyConflict:
                    return "blocked", status
                unit.execute(
                    "UPDATE managed_master_key SET source='environment',generation=1, "
                    "revision=revision+1,key_check=? WHERE singleton=1 AND source='unselected'",
                    (_check(env_key),),
                )
                unit.execute("UPDATE secret_receipts SET key_generation=1")
                return "selected", None
            if inventory["live"] or inventory["names"] or inventory["key_names"]:
                return "blocked", status
            operation_id = secrets.token_hex(16)
            key_id = secrets.token_hex(16)
            unit.execute(
                "INSERT INTO managed_master_key_operations "
                "(operation_id,kind,state,candidate_key_id,expected_generation,expected_revision,inventory_digest) "
                "VALUES(?,'initialize','preparing',?,?,?,?)",
                (
                    operation_id,
                    key_id,
                    row["generation"],
                    row["revision"],
                    inventory["digest"],
                ),
            )
            return "create", operation_id

        action, detail = await self.database.executor.run_transaction(
            prepare, begin_mode="IMMEDIATE"
        )
        if action == "create":
            await self._activate_empty_candidate(str(detail))
        status = await self.status()
        if status["ready"]:
            self._generation, self._revision = status["generation"], status["revision"]
        return status

    async def _activate_empty_candidate(
        self,
        operation_id: str,
        *,
        fence: Callable[[SQLiteUnitOfWork], None] | None = None,
    ) -> None:
        operation_id = await self._prepare_candidate(
            operation_id, fence=fence, allow_completed=True
        )
        if not operation_id:
            return

        def activate(unit: SQLiteUnitOfWork) -> None:
            if fence is not None:
                fence(unit)
            row = self._selection(unit)
            pending = self._pending(unit)
            if pending is None and row["source"] == "managed":
                return
            if pending is None or pending["operation_id"] != operation_id:
                raise ManagedKeyConflict("operation_changed")
            inventory = self._inventory(unit)
            if (
                (row["generation"], row["revision"], inventory["digest"])
                != (
                    pending["expected_generation"],
                    pending["expected_revision"],
                    pending["inventory_digest"],
                )
                or inventory["unknown"]
                or inventory["live"]
                or inventory["names"]
            ):
                raise ManagedKeyConflict("stale_inventory")
            key_id = str(pending["candidate_key_id"])
            if f"master-{key_id}.key" in inventory["key_names"]:
                key = self.files.confirm_durable(key_id)
            else:
                key = secrets.token_bytes(32)
                self.files.create(key_id, key)
            if self._environment_key()[0]:
                raise ManagedKeyConflict("environment_key_conflict")
            unit.execute(
                "UPDATE managed_master_key SET source='managed',generation=generation+1, "
                "revision=revision+1,key_id=?,key_check=? WHERE singleton=1 "
                "AND source='unselected' AND generation=? AND revision=?",
                (key_id, _check(key), row["generation"], row["revision"]),
            )
            unit.execute(
                "UPDATE managed_master_key_operations SET state='committed' "
                "WHERE operation_id=? AND state='preparing'",
                (pending["operation_id"],),
            )

        await self.database.executor.run_transaction(activate, begin_mode="IMMEDIATE")

    async def restore(
        self,
        encoded_key: str,
        *,
        expected_revision: int,
        expected_generation: int,
        inventory_digest: str,
        fence: Callable[[SQLiteUnitOfWork], None] | None = None,
    ) -> dict:
        key = _decode_key(encoded_key)

        def prepare(unit: SQLiteUnitOfWork):
            if fence is not None:
                fence(unit)
            status = self._status_in(unit)
            self._expect(
                status, expected_revision, expected_generation, inventory_digest
            )
            if status["unknown_count"]:
                raise ManagedKeyConflict("inventory_unknown")
            row = self._selection(unit)
            present, env_key, env_error = self._environment_key()
            if env_error is not None or present and env_key != key:
                raise ManagedKeyConflict("environment_key_conflict")
            if row["source"] != "unselected" and not hmac.compare_digest(
                _check(key), row["key_check"]
            ):
                raise ManagedKeyConflict("key_conflict")
            pending = self._pending(unit)
            if pending is not None:
                if (
                    pending["kind"],
                    pending["expected_revision"],
                    pending["expected_generation"],
                    pending["inventory_digest"],
                ) != (
                    "restore",
                    expected_revision,
                    expected_generation,
                    inventory_digest,
                ):
                    raise ManagedKeyConflict("operation_incomplete")
                self._decrypt_all(key, self._inventory(unit))
                return "candidate", str(pending["operation_id"])
            inventory = self._inventory(unit)
            self._decrypt_all(key, inventory)
            if row["source"] == "environment" or present:
                if not present or env_key is None:
                    raise ManagedKeyConflict("environment_key_missing")
                if row["source"] == "managed":
                    operation_id = secrets.token_hex(16)
                    unit.execute(
                        "INSERT INTO managed_master_key_operations "
                        "(operation_id,kind,state,candidate_key_id,expected_generation,"
                        "expected_revision,inventory_digest) "
                        "VALUES(?,'restore','committed',NULL,?,?,?)",
                        (
                            operation_id,
                            expected_generation,
                            expected_revision,
                            inventory_digest,
                        ),
                    )
                    unit.execute(
                        "INSERT INTO managed_master_key_retired"
                        "(key_id,retired_generation,reset_operation_id) "
                        "VALUES(?,?,?)",
                        (row["key_id"], row["generation"], operation_id),
                    )
                unit.execute(
                    "UPDATE managed_master_key SET source='environment',generation=CASE WHEN "
                    "generation=0 THEN 1 ELSE generation END,revision=revision+1, "
                    "key_id=NULL,key_check=? WHERE singleton=1 AND generation=? AND revision=?",
                    (_check(key), expected_generation, expected_revision),
                )
                if expected_generation == 0:
                    unit.execute("UPDATE secret_receipts SET key_generation=1")
                return "selected", None
            operation_id = secrets.token_hex(16)
            # Never overwrite a damaged selected file. A new private candidate
            # can be activated only after the supplied key decrypts every live
            # payload; the previous name remains as an explicit retired file.
            key_id = secrets.token_hex(16)
            unit.execute(
                "INSERT INTO managed_master_key_operations "
                "(operation_id,kind,state,candidate_key_id,expected_generation,expected_revision,inventory_digest) "
                "VALUES(?,'restore','preparing',?,?,?,?)",
                (
                    operation_id,
                    key_id,
                    expected_generation,
                    expected_revision,
                    inventory_digest,
                ),
            )
            return "candidate", operation_id

        action, operation_id = await self.database.executor.run_transaction(
            prepare, begin_mode="IMMEDIATE"
        )
        if action == "candidate":
            operation_id = await self._prepare_candidate(str(operation_id), fence=fence)

            def activate(unit: SQLiteUnitOfWork) -> None:
                if fence is not None:
                    fence(unit)
                row, pending = self._selection(unit), self._pending(unit)
                if pending is None or pending["operation_id"] != operation_id:
                    raise ManagedKeyConflict("operation_changed")
                inventory = self._inventory(unit)
                if (row["generation"], row["revision"], inventory["digest"]) != (
                    expected_generation,
                    expected_revision,
                    pending["inventory_digest"],
                ) or inventory["unknown"]:
                    raise ManagedKeyConflict("stale_inventory")
                present, env_key, env_error = self._environment_key()
                if present or env_error is not None or env_key is not None:
                    raise ManagedKeyConflict("environment_key_conflict")
                self._decrypt_all(key, inventory)
                key_id = str(pending["candidate_key_id"])
                if f"master-{key_id}.key" in inventory["key_names"]:
                    durable_key = self.files.confirm_durable(key_id)
                    if not hmac.compare_digest(durable_key, key):
                        raise ManagedKeyConflict("key_conflict")
                else:
                    self.files.create(key_id, key)
                if row["source"] == "managed" and row["key_id"] != key_id:
                    unit.execute(
                        "INSERT INTO managed_master_key_retired"
                        "(key_id,retired_generation,reset_operation_id) "
                        "VALUES(?,?,?)",
                        (row["key_id"], row["generation"], operation_id),
                    )
                unit.execute(
                    "UPDATE managed_master_key SET source='managed',generation=CASE WHEN "
                    "generation=0 THEN 1 ELSE generation END,revision=revision+1, "
                    "key_id=?,key_check=? WHERE singleton=1 AND generation=? AND revision=?",
                    (key_id, _check(key), expected_generation, expected_revision),
                )
                if expected_generation == 0:
                    unit.execute("UPDATE secret_receipts SET key_generation=1")
                unit.execute(
                    "UPDATE managed_master_key_operations SET state='committed' "
                    "WHERE operation_id=? AND state='preparing'",
                    (operation_id,),
                )

            await self.database.executor.run_transaction(
                activate, begin_mode="IMMEDIATE"
            )
        status = await self.status()
        if status["ready"]:
            self._generation, self._revision = status["generation"], status["revision"]
        return status

    async def retry(
        self,
        *,
        expected_revision: int,
        expected_generation: int,
        inventory_digest: str,
        fence: Callable[[SQLiteUnitOfWork], None] | None = None,
    ) -> dict:
        def inspect(unit: SQLiteUnitOfWork):
            if fence is not None:
                fence(unit)
            status = self._status_in(unit)
            self._expect(
                status, expected_revision, expected_generation, inventory_digest
            )
            pending = self._pending(unit)
            if pending is None:
                return None
            if pending["kind"] != "initialize":
                raise ManagedKeyConflict("operation_requires_confirmation")
            return str(pending["operation_id"])

        pending_id = await self.database.executor.run_read(inspect)
        if pending_id is not None:
            await self._activate_empty_candidate(pending_id, fence=fence)
            status = await self.status()
            if status["ready"]:
                self._generation, self._revision = (
                    status["generation"],
                    status["revision"],
                )
            return status
        return await self.startup()

    async def reset(
        self,
        *,
        expected_revision: int,
        expected_generation: int,
        inventory_digest: str,
        confirmation: str,
        fence: Callable[[SQLiteUnitOfWork], None] | None = None,
    ) -> dict:
        if confirmation != _RESET_CONFIRMATION:
            raise ManagedKeyConflict("confirmation_required")

        def prepare(unit: SQLiteUnitOfWork):
            if fence is not None:
                fence(unit)
            status = self._status_in(unit)
            self._expect(
                status, expected_revision, expected_generation, inventory_digest
            )
            if status["unknown_count"]:
                raise ManagedKeyConflict("inventory_unknown")
            row = self._selection(unit)
            if row["source"] == "environment" or self._environment_key()[0]:
                raise ManagedKeyConflict("environment_managed_externally")
            pending = self._pending(unit)
            if pending is not None:
                if (
                    pending["kind"],
                    pending["expected_revision"],
                    pending["expected_generation"],
                    pending["inventory_digest"],
                ) != (
                    "reset",
                    expected_revision,
                    expected_generation,
                    inventory_digest,
                ):
                    raise ManagedKeyConflict("operation_incomplete")
                return str(pending["operation_id"])
            operation_id, key_id = secrets.token_hex(16), secrets.token_hex(16)
            unit.execute(
                "INSERT INTO managed_master_key_operations "
                "(operation_id,kind,state,candidate_key_id,expected_generation,expected_revision,inventory_digest) "
                "VALUES(?,'reset','preparing',?,?,?,?)",
                (
                    operation_id,
                    key_id,
                    expected_generation,
                    expected_revision,
                    inventory_digest,
                ),
            )
            return operation_id

        operation_id = await self.database.executor.run_transaction(
            prepare, begin_mode="IMMEDIATE"
        )
        operation_id = await self._prepare_candidate(operation_id, fence=fence)

        def activate(unit: SQLiteUnitOfWork) -> None:
            if fence is not None:
                fence(unit)
            row, pending = self._selection(unit), self._pending(unit)
            if pending is None or pending["operation_id"] != operation_id:
                raise ManagedKeyConflict("operation_changed")
            inventory = self._inventory(unit)
            if (row["generation"], row["revision"], inventory["digest"]) != (
                expected_generation,
                expected_revision,
                pending["inventory_digest"],
            ) or inventory["unknown"]:
                raise ManagedKeyConflict("stale_inventory")
            if self._environment_key()[0]:
                raise ManagedKeyConflict("environment_managed_externally")
            key_id = str(pending["candidate_key_id"])
            if f"master-{key_id}.key" in inventory["key_names"]:
                key = self.files.confirm_durable(key_id)
            else:
                key = secrets.token_bytes(32)
                self.files.create(key_id, key)
            if row["source"] == "managed" and row["key_id"] != key_id:
                unit.execute(
                    "INSERT INTO managed_master_key_retired(key_id,retired_generation,reset_operation_id) "
                    "VALUES(?,?,?)",
                    (row["key_id"], row["generation"], operation_id),
                )
            unit.execute(
                "UPDATE config_state SET revision=revision+1 WHERE EXISTS("
                "SELECT 1 FROM config_entries AS e WHERE e.principal_id=config_state.principal_id "
                "AND e.module_id=config_state.module_id AND e.secret_token IS NOT NULL)"
            )
            unit.execute(
                "UPDATE config_entries SET secret_token=NULL,secret_principal_id=NULL, "
                "secret_module_id=NULL,secret_field=NULL,secret_operation_id=NULL, "
                "secret_state='tombstoned',revision=revision+1 WHERE secret_token IS NOT NULL"
            )
            unit.execute(
                "UPDATE grants SET secret_token=NULL,secret_principal_id=NULL, "
                "secret_module_id=NULL,secret_field=NULL,secret_operation_id=NULL, "
                "status=CASE WHEN status='active' THEN 'unavailable' ELSE status END, "
                "revision=revision+1 WHERE secret_token IS NOT NULL"
            )
            unit.execute(
                "UPDATE login_sessions SET status='cancelled' WHERE status='pending'"
            )
            unit.execute(
                "UPDATE secret_receipts SET state='tombstoned',ledger_revision=ledger_revision+1 "
                "WHERE state!='tombstoned'"
            )
            unit.execute(
                "UPDATE managed_master_key SET source='managed',generation=generation+1, "
                "revision=revision+1,key_id=?,key_check=? WHERE singleton=1 "
                "AND generation=? AND revision=?",
                (key_id, _check(key), expected_generation, expected_revision),
            )
            unit.execute(
                "UPDATE managed_master_key_operations SET state='committed' "
                "WHERE operation_id=? AND state='preparing'",
                (operation_id,),
            )

        await self.database.executor.run_transaction(activate, begin_mode="IMMEDIATE")
        status = await self.status()
        if status["ready"]:
            self._generation, self._revision = status["generation"], status["revision"]
        return status


__all__ = ["ManagedKeyConflict", "ManagedKeyManager"]
