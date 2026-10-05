"""SQLite repositories for module configuration and declared records.

The classes in this module deliberately expose only the frozen B03 repository
ports.  A module receives a collection that is already bound to its module,
declaration, and owner scope; it never receives a connection or SQL string.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from ...api.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ...api.services import (
    ConfigPatchMode,
    ConfigSnapshot,
    ConfigTarget,
    PersistedConfigPatch,
)
from ...api.storage import (
    CollectionDescriptor,
    DeclaredIndexQuery,
    OwnerScope,
    OwnershipKind,
    QueryOperator,
    RecordPage,
    SecretMetadata,
    SecretMetadataState,
    SecretRef,
    VersionedRecord,
    freeze_json,
)
from ...core.ports import (
    ModuleNotRegistered,
    ModuleRegistrationLookup,
    ModuleRegistrationSnapshot,
    RevisionConflict,
    UniqueConstraintViolation,
)
from ...core.registry import RegisteredModule
from .database import SQLiteDatabase, SQLiteUnitOfWork
from .repositories_admin_credentials import assert_generation_current


def _database(value: SQLiteDatabase | str | Path) -> SQLiteDatabase:
    if isinstance(value, SQLiteDatabase):
        return value
    if isinstance(value, (str, Path)):
        return SQLiteDatabase(value)
    raise TypeError("database must be a SQLiteDatabase or path")


def _plain(value: object) -> object:
    """Turn frozen JSON values into values accepted by ``json.dumps``."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _json(value: object) -> str:
    try:
        return json.dumps(
            _plain(freeze_json(value)),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        raise ValueError("value is not valid JSON") from None


def _load_json(raw: str, *, object_required: bool = True) -> object:
    if not isinstance(raw, str):
        raise ValueError("stored JSON is invalid")
    try:
        value = json.loads(raw)
        value = freeze_json(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("stored JSON is invalid") from None
    if object_required and not isinstance(value, Mapping):
        raise ValueError("stored JSON object is invalid")
    return value


def _bounded(value: str, field: str) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or any(char in value for char in ("/", "\\", "\n", "\r"))
    ):
        raise ValueError(f"{field} is invalid")
    return value


def _module_id(value: str) -> str:
    """Validate a registry-issued module id, including package namespaces."""
    if (
        type(value) is not str
        or not value.strip()
        or any(char in value for char in ("\\", "\n", "\r"))
    ):
        raise ValueError("module id is invalid")
    return value


def _config_target(value: ConfigTarget) -> ConfigTarget:
    return ConfigTarget.validate(value)


def _secret_metadata(row: sqlite3.Row) -> SecretMetadata:
    token = row["secret_token"]
    ref = None
    state = row["secret_state"]
    if token is not None:
        try:
            ref = SecretRef(
                token,
                row["secret_principal_id"],
                row["secret_module_id"],
                row["secret_field"],
                row["secret_operation_id"],
            )
        except (TypeError, ValueError):
            raise ValueError("stored secret metadata is invalid") from None
    try:
        metadata_state = SecretMetadataState(state)
        return SecretMetadata(row["field"], ref, int(row["revision"]), metadata_state)
    except (TypeError, ValueError):
        raise ValueError("stored secret metadata is invalid") from None


class SQLiteConfigRepository:
    """Persist ``ConfigSnapshot`` values and metadata with target-scoped CAS."""

    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        *,
        subscription_gate_fields: Mapping[ConfigTarget, tuple[str, ...]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.database = _database(database)
        if subscription_gate_fields is not None and not isinstance(
            subscription_gate_fields, Mapping
        ):
            raise ValueError("subscription gate policy must be a mapping")
        policy: dict[ConfigTarget, tuple[str, ...]] = {}
        for target, fields in (subscription_gate_fields or {}).items():
            target = _config_target(target)
            if (
                type(fields) is not tuple
                or not fields
                or len(set(fields)) != len(fields)
            ):
                raise ValueError("subscription gate policy fields are invalid")
            policy[target] = tuple(_bounded(field, "config field") for field in fields)
        if not callable(clock):
            raise ValueError("subscription gate clock must be callable")
        # Composition must validate the complete trusted map before constructing
        # this repository. No module-supplied patch can extend this policy.
        self._subscription_gate_fields = MappingProxyType(policy)
        self._clock = clock

    def _subscription_timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("subscription transition requires an aware datetime")
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _gate_value(row: sqlite3.Row | None) -> bool | None:
        if row is None or row["value_json"] is None:
            return None
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            return None
        return value if type(value) is bool else None

    @staticmethod
    def _valid_transition(raw: object) -> bool:
        if type(raw) is not str:
            return False
        try:
            value = datetime.fromisoformat(raw)
            return value.utcoffset() == timedelta(0)
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _bootstrap_phase(unit: SQLiteUnitOfWork) -> str | None:
        row = unit.execute(
            "SELECT phase FROM subscription_gate_bootstrap WHERE singleton=1"
        ).fetchone()
        return None if row is None else row[0]

    def _subscription_gates_valid(self, unit: SQLiteUnitOfWork) -> bool:
        if self._bootstrap_phase(unit) != "complete":
            return False
        for target, fields in self._subscription_gate_fields.items():
            for field in fields:
                marker = unit.execute(
                    "SELECT 1 FROM subscription_gate_initializations "
                    "WHERE principal_id=? AND module_id=? AND field=?",
                    (target.principal_id, target.module_id, field),
                ).fetchone()
                row = unit.execute(
                    "SELECT value_json,subscription_transition_at FROM config_entries "
                    "WHERE principal_id=? AND module_id=? AND field=?",
                    (target.principal_id, target.module_id, field),
                ).fetchone()
                value = self._gate_value(row)
                if marker is None or value is None:
                    return False
                if value and not self._valid_transition(
                    row["subscription_transition_at"]
                ):
                    return False
        return True

    async def initialize_subscription_gates(self) -> bool:
        """Consume one migration-owned bootstrap using the full trusted policy.

        After completion this only validates exact markers and gate rows; it
        never recreates a deleted field, marker, or bootstrap singleton. Invalid
        existing values remain untouched and produce a closed validation result.
        """
        if not self._subscription_gate_fields:
            return False

        def inspect(unit: SQLiteUnitOfWork) -> tuple[str | None, bool]:
            phase = self._bootstrap_phase(unit)
            return phase, self._subscription_gates_valid(unit)

        phase, valid = await self.database.executor.run_read(inspect)
        if phase != "pending":
            return valid

        def initialize(unit: SQLiteUnitOfWork) -> bool:
            phase = self._bootstrap_phase(unit)
            if phase != "pending":
                return self._subscription_gates_valid(unit)
            if (
                unit.execute(
                    "SELECT 1 FROM subscription_gate_initializations LIMIT 1"
                ).fetchone()
                is not None
            ):
                return False
            transition = self._subscription_timestamp()
            for target, fields in self._subscription_gate_fields.items():
                rows = {
                    field: unit.execute(
                        "SELECT value_json,subscription_transition_at FROM config_entries "
                        "WHERE principal_id=? AND module_id=? AND field=?",
                        (target.principal_id, target.module_id, field),
                    ).fetchone()
                    for field in fields
                }
                missing = [field for field, row in rows.items() if row is None]
                if missing:
                    state = unit.execute(
                        "SELECT revision FROM config_state "
                        "WHERE principal_id=? AND module_id=?",
                        (target.principal_id, target.module_id),
                    ).fetchone()
                    revision = 1 if state is None else int(state[0]) + 1
                    unit.execute(
                        "INSERT INTO config_state(principal_id,module_id,revision) "
                        "VALUES (?,?,?) ON CONFLICT(principal_id,module_id) "
                        "DO UPDATE SET revision=excluded.revision",
                        (target.principal_id, target.module_id, revision),
                    )
                    for field in missing:
                        unit.execute(
                            "INSERT INTO config_entries "
                            "(principal_id,module_id,field,value_json,revision,"
                            "subscription_transition_at) VALUES (?,?,?,'true',?,?)",
                            (
                                target.principal_id,
                                target.module_id,
                                field,
                                revision,
                                transition,
                            ),
                        )
                for field, row in rows.items():
                    if (
                        self._gate_value(row) is True
                        and row["subscription_transition_at"] is None
                    ):
                        unit.execute(
                            "UPDATE config_entries SET subscription_transition_at=? "
                            "WHERE principal_id=? AND module_id=? AND field=?",
                            (transition, target.principal_id, target.module_id, field),
                        )
                    unit.execute(
                        "INSERT INTO subscription_gate_initializations "
                        "(principal_id,module_id,field) VALUES (?,?,?)",
                        (target.principal_id, target.module_id, field),
                    )
            unit.execute(
                "UPDATE subscription_gate_bootstrap SET phase='complete' WHERE singleton=1"
            )
            return self._subscription_gates_valid(unit)

        return await self.database.executor.run_transaction(
            initialize, begin_mode="IMMEDIATE"
        )

    async def current(
        self, target: ConfigTarget, *, grant=None, operation=None
    ) -> ConfigSnapshot:
        target = _config_target(target)

        def read(unit: SQLiteUnitOfWork) -> ConfigSnapshot:
            fields = None
            if grant is not None:
                if operation is not AdminOperation.READ_CONFIG:
                    raise AdminAuthorizationDenied
                assert_generation_current(unit, grant, operation)
                fields = grant._effect.resource_fields(target)
            self._ensure_state(unit, target)
            rows = unit.execute(
                "SELECT * FROM config_entries WHERE principal_id = ? AND module_id = ? "
                "ORDER BY field",
                (target.principal_id, target.module_id),
            ).fetchall()
            state = unit.execute(
                "SELECT revision FROM config_state WHERE principal_id = ? AND module_id = ?",
                (target.principal_id, target.module_id),
            ).fetchone()
            if fields is not None:
                rows = [row for row in rows if row["field"] in fields]
            return self._snapshot(target, int(state[0]), rows)

        return await self.database.executor.run_transaction(read)

    async def update(
        self, target: ConfigTarget, patch: PersistedConfigPatch
    ) -> ConfigSnapshot:
        target = _config_target(target)
        patch = PersistedConfigPatch.validate_for(target, patch)
        return await self._run_update(target, patch)

    async def update_authorized(
        self,
        target: ConfigTarget,
        patch: PersistedConfigPatch,
        grant: AdminAuthorizationGrant,
    ) -> ConfigSnapshot:
        target = _config_target(target)
        patch = PersistedConfigPatch.validate_for(target, patch)
        if not isinstance(grant, AdminAuthorizationGrant):
            raise AdminAuthorizationDenied from None
        return await self._run_update(target, patch, grant=grant)

    async def _run_update(
        self,
        target: ConfigTarget,
        patch: PersistedConfigPatch,
        *,
        grant: AdminAuthorizationGrant | None = None,
    ) -> ConfigSnapshot:
        def apply(unit: SQLiteUnitOfWork) -> ConfigSnapshot:
            if grant is not None:
                assert_generation_current(unit, grant, AdminOperation.UPDATE_CONFIG)
                grant._effect.check_resource(
                    target, (update.field for update in patch.updates)
                )
                for update in patch.updates:
                    receipt = update.receipt
                    if receipt is None:
                        continue
                    row = unit.execute(
                        "SELECT principal_id, module_id, field, operation_id, "
                        "expected_config_revision, ledger_revision, state "
                        "FROM secret_receipts WHERE secret_token=?",
                        (receipt.secret_ref.token,),
                    ).fetchone()
                    if (
                        row is None
                        or row["principal_id"] != target.principal_id
                        or row["module_id"] != target.module_id
                        or row["field"] != update.field
                        or row["operation_id"] != patch.operation_id
                        or int(row["expected_config_revision"])
                        != patch.expected_revision
                        or int(row["ledger_revision"]) != receipt.ledger_revision + 1
                        or row["state"] != "claimed"
                    ):
                        raise AdminAuthorizationDenied from None
            snapshot = self._update_in_unit(unit, target, patch)
            return (
                snapshot if grant is None else grant._effect.project_snapshot(snapshot)
            )

        return await self.database.executor.run_transaction(
            apply, begin_mode="IMMEDIATE"
        )

    def _update_in_unit(
        self, unit: SQLiteUnitOfWork, target: ConfigTarget, patch: PersistedConfigPatch
    ) -> ConfigSnapshot:
        gate_fields = self._subscription_gate_fields.get(target, ())
        for update in patch.updates:
            if update.field not in gate_fields:
                continue
            declaration = next(
                field for field in patch.declared_fields if field.name == update.field
            )
            if declaration.sensitive or update.mode is ConfigPatchMode.CLEAR:
                raise ValueError(
                    "subscription gate requires KEEP or strict bool REPLACE"
                )
            if (
                update.mode is ConfigPatchMode.REPLACE
                and type(update.value) is not bool
            ):
                raise ValueError(
                    "subscription gate requires KEEP or strict bool REPLACE"
                )
        self._ensure_state(unit, target)
        row = unit.execute(
            "SELECT revision FROM config_state WHERE principal_id = ? AND module_id = ?",
            (target.principal_id, target.module_id),
        ).fetchone()
        actual = int(row[0])
        if actual != patch.expected_revision:
            raise RevisionConflict("config", patch.expected_revision, actual)
        next_revision = actual + 1
        unit.execute(
            "UPDATE config_state SET revision = ? WHERE principal_id = ? AND module_id = ? "
            "AND revision = ?",
            (next_revision, target.principal_id, target.module_id, actual),
        )
        for update in patch.updates:
            declaration = next(
                field for field in patch.declared_fields if field.name == update.field
            )
            if update.mode is ConfigPatchMode.KEEP:
                continue
            if update.field in gate_fields:
                existing = unit.execute(
                    "SELECT value_json FROM config_entries "
                    "WHERE principal_id=? AND module_id=? AND field=?",
                    (target.principal_id, target.module_id, update.field),
                ).fetchone()
                if self._gate_value(existing) is update.value:
                    continue
                unit.execute(
                    "INSERT INTO config_entries "
                    "(principal_id,module_id,field,value_json,revision,"
                    "subscription_transition_at) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(principal_id,module_id,field) DO UPDATE SET "
                    "value_json=excluded.value_json,secret_token=NULL,"
                    "secret_principal_id=NULL,secret_module_id=NULL,secret_field=NULL,"
                    "secret_operation_id=NULL,secret_state=NULL,revision=excluded.revision,"
                    "subscription_transition_at=excluded.subscription_transition_at",
                    (
                        target.principal_id,
                        target.module_id,
                        update.field,
                        _json(update.value),
                        next_revision,
                        self._subscription_timestamp(),
                    ),
                )
                continue
            if update.mode is ConfigPatchMode.CLEAR:
                if declaration.sensitive:
                    unit.execute(
                        "INSERT INTO config_entries "
                        "(principal_id,module_id,field,value_json,secret_token,"
                        "secret_principal_id,secret_module_id,secret_field,"
                        "secret_operation_id,secret_state,revision) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(principal_id,module_id,field) DO UPDATE SET "
                        "value_json=NULL,secret_token=NULL,secret_principal_id=NULL,"
                        "secret_module_id=NULL,secret_field=NULL,secret_operation_id=NULL,"
                        "secret_state='tombstoned',revision=excluded.revision",
                        (
                            target.principal_id,
                            target.module_id,
                            update.field,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            "tombstoned",
                            next_revision,
                        ),
                    )
                else:
                    unit.execute(
                        "DELETE FROM config_entries WHERE principal_id = ? AND module_id = ? "
                        "AND field = ?",
                        (target.principal_id, target.module_id, update.field),
                    )
                continue
            if declaration.sensitive:
                receipt = update.receipt
                if receipt is None:
                    raise ValueError("sensitive persisted replace requires one receipt")
                ref = receipt.secret_ref
                unit.execute(
                    "INSERT INTO config_entries "
                    "(principal_id,module_id,field,value_json,secret_token,"
                    "secret_principal_id,secret_module_id,secret_field,"
                    "secret_operation_id,secret_state,revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(principal_id,module_id,field) DO UPDATE SET "
                    "value_json=NULL,secret_token=excluded.secret_token,"
                    "secret_principal_id=excluded.secret_principal_id,"
                    "secret_module_id=excluded.secret_module_id,secret_field=excluded.secret_field,"
                    "secret_operation_id=excluded.secret_operation_id,secret_state='active',"
                    "revision=excluded.revision",
                    (
                        target.principal_id,
                        target.module_id,
                        update.field,
                        None,
                        ref.token,
                        ref.principal_id,
                        ref.module_id,
                        ref.field,
                        ref.operation_id,
                        "active",
                        next_revision,
                    ),
                )
            else:
                if update.value is None:
                    raise ValueError("ordinary persisted replace requires a value")
                unit.execute(
                    "INSERT INTO config_entries "
                    "(principal_id,module_id,field,value_json,secret_token,"
                    "secret_principal_id,secret_module_id,secret_field,"
                    "secret_operation_id,secret_state,revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(principal_id,module_id,field) DO UPDATE SET "
                    "value_json=excluded.value_json,secret_token=NULL,"
                    "secret_principal_id=NULL,secret_module_id=NULL,secret_field=NULL,"
                    "secret_operation_id=NULL,secret_state=NULL,revision=excluded.revision",
                    (
                        target.principal_id,
                        target.module_id,
                        update.field,
                        _json(update.value),
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        next_revision,
                    ),
                )
        rows = unit.execute(
            "SELECT * FROM config_entries WHERE principal_id = ? AND module_id = ? "
            "ORDER BY field",
            (target.principal_id, target.module_id),
        ).fetchall()
        return self._snapshot(target, next_revision, rows)

    async def metadata(self, target: ConfigTarget, field: str) -> SecretMetadata | None:
        target = _config_target(target)
        field = _bounded(field, "config field")

        def _worker(unit):
            row = unit.execute(
                "SELECT * FROM config_entries WHERE principal_id = ? AND module_id = ? AND field = ?",
                (target.principal_id, target.module_id, field),
            ).fetchone()
            if row is None or row["secret_state"] is None:
                return None
            return _secret_metadata(row)

        return await self.database.executor.run_read(_worker)

    @staticmethod
    def _ensure_state(unit: SQLiteUnitOfWork, target: ConfigTarget) -> None:
        unit.execute(
            "INSERT INTO config_state(principal_id,module_id,revision) VALUES (?,?,1) "
            "ON CONFLICT(principal_id,module_id) DO NOTHING",
            (target.principal_id, target.module_id),
        )

    @staticmethod
    def _snapshot(
        target: ConfigTarget, revision: int, rows: list[sqlite3.Row]
    ) -> ConfigSnapshot:
        values: dict[str, object] = {}
        metadata: list[SecretMetadata] = []
        for row in rows:
            if row["value_json"] is not None:
                values[row["field"]] = _load_json(
                    row["value_json"], object_required=False
                )
            elif row["secret_state"] is not None:
                metadata.append(_secret_metadata(row))
        return ConfigSnapshot(revision, values, tuple(metadata), target)


def _scope_key(scope: OwnerScope) -> tuple[str, str, str, int]:
    scope = OwnerScope.validate(scope)
    if scope.kind is OwnershipKind.PUBLIC:
        return (scope.kind.value, "", "", 0)
    if scope.kind is OwnershipKind.USER:
        return (scope.kind.value, scope.user_id or "", "", 0)
    grant = scope.grant
    if grant is None:
        raise ValueError("authorized scope requires a grant")
    return (scope.kind.value, scope.user_id or "", grant.grant_id, grant.revision)


def _descriptor(value: CollectionDescriptor) -> CollectionDescriptor:
    if not isinstance(value, CollectionDescriptor):
        raise TypeError("collection descriptor is required")
    try:
        return CollectionDescriptor.validate(value)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("collection descriptor is invalid") from None


class SQLiteRecordRepository:
    """Issue collections only from a registry-validated module snapshot."""

    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        registered_module: RegisteredModule,
        registration_lookup: ModuleRegistrationLookup,
    ) -> None:
        self.database = _database(database)
        if not isinstance(registered_module, RegisteredModule):
            raise TypeError("repository requires a registry-validated module")
        try:
            require_registered = getattr(registration_lookup, "require_registered")
        except Exception:
            raise TypeError(
                "repository requires a module registration lookup"
            ) from None
        if not callable(require_registered):
            raise TypeError("repository requires a module registration lookup")
        self.module_id = _module_id(registered_module.module_id)
        self.registered_module = registered_module
        self.registration_lookup = registration_lookup

    async def collection(
        self, module_id: str, descriptor: CollectionDescriptor, owner: OwnerScope
    ) -> "SQLiteRecordCollection":
        module_id = _module_id(module_id)
        if module_id != self.module_id:
            raise ValueError("collection module is outside repository binding")
        registration = await self._current_registration(module_id)
        self._validate_registration(registration)
        descriptor = _descriptor(descriptor)
        declared = next(
            (item for item in registration.collections if item.name == descriptor.name),
            None,
        )
        if declared is None or declared != descriptor:
            raise ValueError("collection is not declared for this module")
        descriptor = declared
        owner = OwnerScope.validate(owner)
        if descriptor.owner_kind is not owner.kind:
            raise ValueError("collection owner scope does not match declaration")
        indexes = {index.name: index.field for index in descriptor.indexes}

        def register(unit: SQLiteUnitOfWork) -> None:
            row = unit.execute(
                "SELECT schema_version,owner_kind,indexes_json FROM module_collections "
                "WHERE module_id = ? AND collection = ?",
                (module_id, descriptor.name),
            ).fetchone()
            encoded = _json(indexes)
            if row is None:
                try:
                    unit.execute(
                        "INSERT INTO module_collections "
                        "(module_id,collection,schema_version,owner_kind,indexes_json) "
                        "VALUES (?,?,?,?,?)",
                        (
                            module_id,
                            descriptor.name,
                            descriptor.schema_version,
                            descriptor.owner_kind.value,
                            encoded,
                        ),
                    )
                except UniqueConstraintViolation:
                    raise ValueError(
                        "collection declaration changed concurrently"
                    ) from None
            else:
                try:
                    existing = (
                        int(row["schema_version"]),
                        OwnershipKind(row["owner_kind"]),
                        _load_json(row["indexes_json"]),
                    )
                except (TypeError, ValueError):
                    raise ValueError(
                        "stored collection declaration is invalid"
                    ) from None
                if existing != (
                    descriptor.schema_version,
                    descriptor.owner_kind,
                    freeze_json(indexes),
                ):
                    raise ValueError("collection declaration does not match")

        await self.database.executor.run_transaction(register, begin_mode="IMMEDIATE")
        return SQLiteRecordCollection(
            self.database,
            module_id,
            descriptor,
            owner,
            self.registration_lookup,
            registration,
        )

    async def _current_registration(self, module_id: str) -> ModuleRegistrationSnapshot:
        try:
            snapshot = await self.registration_lookup.require_registered(module_id)
            return ModuleRegistrationSnapshot(
                snapshot.module_id,
                snapshot.enabled,
                snapshot.registry_revision,
                snapshot.epoch,
                snapshot.collections,
            )
        except ModuleNotRegistered:
            raise
        except (AttributeError, TypeError, ValueError):
            raise ValueError(
                "module registration lookup returned an invalid snapshot"
            ) from None

    def _validate_registration(self, snapshot: ModuleRegistrationSnapshot) -> None:
        if snapshot.module_id != self.module_id:
            raise ValueError("module registration does not match repository binding")
        if not snapshot.enabled:
            raise ValueError("module is disabled")
        if snapshot.epoch != self.registered_module.epoch:
            raise ValueError("module registration epoch changed")


class SQLiteRecordCollection:
    """A collection whose SQL predicates include its fixed owner key."""

    def __init__(
        self,
        database: SQLiteDatabase,
        module_id: str,
        descriptor: CollectionDescriptor,
        owner: OwnerScope,
        registration_lookup: ModuleRegistrationLookup,
        registration: ModuleRegistrationSnapshot,
    ) -> None:
        self.database = database
        self.module_id = module_id
        self.descriptor = descriptor
        self._scope = OwnerScope.validate(owner)
        self._owner = _scope_key(self._scope)
        self.registration_lookup = registration_lookup
        self._registration = registration

    @property
    def scope(self) -> OwnerScope:
        return self._scope

    async def get(self, key: str) -> VersionedRecord | None:
        await self._ensure_registration()
        key = _bounded(key, "record key")

        def _worker(unit):
            row = self._row(unit, key)
            return None if row is None else self._record(row)

        return await self.database.executor.run_read(_worker)

    async def create(self, key: str, value: Mapping[str, object]) -> VersionedRecord:
        await self._ensure_registration()
        key = _bounded(key, "record key")
        record = VersionedRecord(key, 1, value)

        def _worker(unit):
            try:
                unit.execute(
                    "INSERT INTO module_records "
                    "(module_id,collection,owner_kind,owner_user_id,owner_grant_id,"
                    "owner_grant_revision,record_key,schema_version,payload_json,revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    self._params(record),
                )
            except UniqueConstraintViolation as exc:
                raise UniqueConstraintViolation("module_record") from exc
            return record

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def replace(
        self, key: str, value: Mapping[str, object], *, expected_revision: int
    ) -> VersionedRecord:
        await self._ensure_registration()
        key = _bounded(key, "record key")
        if isinstance(expected_revision, bool) or not isinstance(
            expected_revision, int
        ):
            raise TypeError("expected revision must be an integer")
        if expected_revision < 1:
            raise ValueError("expected revision must be positive")
        record = VersionedRecord(key, expected_revision + 1, value)

        def _worker(unit):
            cursor = unit.execute(
                "UPDATE module_records SET payload_json = ?, revision = ?, schema_version = ? "
                "WHERE module_id=? AND collection=? AND owner_kind=? AND owner_user_id=? "
                "AND owner_grant_id=? AND owner_grant_revision=? AND record_key=? AND revision=?",
                (
                    _json(record.value),
                    record.revision,
                    self.descriptor.schema_version,
                    *self._owner_prefix(),
                    key,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                self._raise_revision(unit, key, expected_revision)
            return record

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def query(self, query: DeclaredIndexQuery) -> RecordPage:
        await self._ensure_registration()
        if not isinstance(query, DeclaredIndexQuery):
            raise TypeError("query must be a DeclaredIndexQuery")
        try:
            query = DeclaredIndexQuery(
                query.index_name,
                query.operator,
                query.value,
                query.limit,
                query.cursor,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("query is invalid") from None
        index = next(
            (item for item in self.descriptor.indexes if item.name == query.index_name),
            None,
        )
        if index is None:
            raise ValueError("index is not declared for this collection")
        offset = self._cursor(query.cursor)
        path = f"$.{index.field}"
        params: list[object] = [
            self.module_id,
            self.descriptor.name,
            *self._owner,
            path,
        ]
        if query.operator is QueryOperator.EQUALS:
            sql = (
                "SELECT * FROM module_records WHERE module_id=? AND collection=? "
                "AND owner_kind=? AND owner_user_id=? AND owner_grant_id=? "
                "AND owner_grant_revision=? AND json_extract(payload_json, ?) = ? "
                "ORDER BY record_key LIMIT ? OFFSET ?"
            )
            params.extend([query.value, query.limit + 1, offset])
        else:
            if not isinstance(query.value, str):
                raise ValueError("prefix queries require a string value")
            escaped = (
                query.value.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            sql = (
                "SELECT * FROM module_records WHERE module_id=? AND collection=? "
                "AND owner_kind=? AND owner_user_id=? AND owner_grant_id=? "
                "AND owner_grant_revision=? AND CAST(json_extract(payload_json, ?) AS TEXT) "
                "LIKE ? ESCAPE '\\' ORDER BY record_key LIMIT ? OFFSET ?"
            )
            params.extend([escaped + "%", query.limit + 1, offset])

        def read(unit: SQLiteUnitOfWork) -> tuple[VersionedRecord, ...]:
            rows = unit.execute(sql, params).fetchall()
            return tuple(self._record(row) for row in rows)

        records = await self.database.executor.run_read(read)
        next_cursor = str(offset + query.limit) if len(records) > query.limit else None
        return RecordPage(records[: query.limit], next_cursor)

    async def delete(self, key: str, *, expected_revision: int) -> None:
        await self._ensure_registration()
        key = _bounded(key, "record key")
        if isinstance(expected_revision, bool) or not isinstance(
            expected_revision, int
        ):
            raise TypeError("expected revision must be an integer")
        if expected_revision < 1:
            raise ValueError("expected revision must be positive")

        def _worker(unit):
            cursor = unit.execute(
                "DELETE FROM module_records WHERE module_id=? AND collection=? "
                "AND owner_kind=? AND owner_user_id=? AND owner_grant_id=? "
                "AND owner_grant_revision=? AND record_key=? AND revision=?",
                (*self._owner_prefix(), key, expected_revision),
            )
            if cursor.rowcount != 1:
                self._raise_revision(unit, key, expected_revision)

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def _ensure_registration(self) -> None:
        try:
            snapshot = await self.registration_lookup.require_registered(self.module_id)
            current = ModuleRegistrationSnapshot(
                snapshot.module_id,
                snapshot.enabled,
                snapshot.registry_revision,
                snapshot.epoch,
                snapshot.collections,
            )
        except ModuleNotRegistered:
            raise
        except (AttributeError, TypeError, ValueError):
            raise ValueError(
                "module registration lookup returned an invalid snapshot"
            ) from None
        if (
            current.module_id != self._registration.module_id
            or current.epoch != self._registration.epoch
            or current.collections != self._registration.collections
        ):
            raise ValueError("module registration changed")
        if not current.enabled:
            raise ValueError("module is disabled")

    def _owner_prefix(self) -> tuple[object, ...]:
        return (self.module_id, self.descriptor.name, *self._owner)

    def _params(self, record: VersionedRecord) -> tuple[object, ...]:
        return (
            *self._owner_prefix(),
            record.key,
            self.descriptor.schema_version,
            _json(record.value),
            record.revision,
        )

    def _row(self, unit: SQLiteUnitOfWork, key: str) -> sqlite3.Row | None:
        return unit.execute(
            "SELECT * FROM module_records WHERE module_id=? AND collection=? "
            "AND owner_kind=? AND owner_user_id=? AND owner_grant_id=? "
            "AND owner_grant_revision=? AND record_key=?",
            (*self._owner_prefix(), key),
        ).fetchone()

    @staticmethod
    def _record(row: sqlite3.Row) -> VersionedRecord:
        try:
            value = _load_json(row["payload_json"])
            return VersionedRecord(row["record_key"], int(row["revision"]), value)
        except (TypeError, ValueError, KeyError):
            raise ValueError("stored record is invalid") from None

    def _raise_revision(
        self, unit: SQLiteUnitOfWork, key: str, expected_revision: int
    ) -> None:
        row = self._row(unit, key)
        actual = 0 if row is None else int(row["revision"])
        raise RevisionConflict("module_record", expected_revision, actual)

    @staticmethod
    def _cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        if not isinstance(cursor, str) or not cursor.isdigit():
            raise ValueError("cursor is invalid")
        try:
            offset = int(cursor)
        except ValueError:
            raise ValueError("cursor is invalid") from None
        if offset < 0 or offset > 2**31:
            raise ValueError("cursor is invalid")
        return offset


# Stable descriptive aliases make the implementation discoverable without
# expanding the frozen public port surface.
ConfigRepository = SQLiteConfigRepository
RecordRepository = SQLiteRecordRepository
ModuleRecordsRepository = SQLiteRecordRepository


__all__ = [
    "ConfigRepository",
    "ModuleRecordsRepository",
    "RecordRepository",
    "SQLiteConfigRepository",
    "SQLiteRecordCollection",
    "SQLiteRecordRepository",
]
