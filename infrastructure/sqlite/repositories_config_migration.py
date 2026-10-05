"""One transaction for ordinary fields, both CAS fences and completion marker."""

from __future__ import annotations

from collections.abc import Mapping

from ...api.administration import AdminOperation
from ...core.ports import RevisionConflict
from ...services.configuration import validate_configuration_value
from .repositories_admin_credentials import assert_generation_current
from .repositories_config import SQLiteConfigRepository, _json, _load_json


class SQLiteOrdinaryConfigurationMigrationRepository:
    def __init__(self, config: SQLiteConfigRepository):
        if not isinstance(config, SQLiteConfigRepository):
            raise TypeError("migration requires the Core config repository")
        self.config = config

    @staticmethod
    def _key(migration):
        return migration.fields[0].target.principal_id, migration.migration_id

    def _marker(self, unit, migration):
        row = unit.execute(
            "SELECT version,snapshot_json FROM ordinary_config_migrations "
            "WHERE principal_id=? AND migration_id=?",
            self._key(migration),
        ).fetchone()
        if row is None:
            return None
        payload = _load_json(row["snapshot_json"])
        expected = tuple(
            (f.target.module_id, f.declaration.name) for f in migration.fields
        )
        if (
            row["version"] != migration.version
            or set(payload)
            not in ({"fields", "legacy"}, {"fields", "legacy", "source"})
            or ("source" in payload and payload["source"] != "admin_replacement")
            or not isinstance(payload["fields"], tuple)
            or len(payload["fields"]) != len(expected)
            or not (
                isinstance(payload["legacy"], Mapping)
                or payload.get("source") == "admin_replacement"
                and payload["legacy"] is None
            )
            or isinstance(payload["legacy"], Mapping)
            and set(payload["legacy"]) - {f.legacy_field for f in migration.fields}
        ):
            raise ValueError("invalid ordinary migration marker")
        for definition, item, (module_id, field) in zip(
            migration.fields, payload["fields"], expected, strict=True
        ):
            if (
                not isinstance(item, Mapping)
                or set(item) != {"module_id", "field", "before", "after"}
                or item["module_id"] != module_id
                or item["field"] != field
                or not isinstance(item["before"], Mapping)
                or not isinstance(item["after"], Mapping)
                or set(item["before"]) - {"value"}
                or set(item["after"]) != {"value", "revision"}
                or type(item["after"].get("revision")) is not int
                or item["after"]["revision"] < 2
            ):
                raise ValueError("invalid ordinary migration marker")
            validate_configuration_value(
                definition.declaration, item["after"]["value"], definition.validator
            )
            if "value" in item["before"]:
                validate_configuration_value(
                    definition.declaration,
                    item["before"]["value"],
                    definition.validator,
                )
        revisions = {}
        for item in payload["fields"]:
            previous = revisions.setdefault(
                item["module_id"], item["after"]["revision"]
            )
            if previous != item["after"]["revision"]:
                raise ValueError("invalid ordinary migration marker revisions")
        return payload

    async def complete(self, migration):
        return await self.config.database.executor.run_read(
            lambda unit: self._marker(unit, migration) is not None
        )

    def _revision(self, unit, target, expected):
        self.config._ensure_state(unit, target)
        actual = int(
            unit.execute(
                "SELECT revision FROM config_state WHERE principal_id=? AND module_id=?",
                (target.principal_id, target.module_id),
            ).fetchone()[0]
        )
        if actual != expected:
            raise RevisionConflict("config", expected, actual)

    @staticmethod
    def _write(unit, target, field, value, revision):
        unit.execute(
            "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) "
            "VALUES(?,?,?,?,?) ON CONFLICT(principal_id,module_id,field) DO UPDATE SET "
            "value_json=excluded.value_json,revision=excluded.revision",
            (target.principal_id, target.module_id, field, _json(value), revision),
        )

    async def apply(self, migration, snapshots, selected, legacy, *, grant=None):
        def apply(unit):
            if grant is not None:
                assert_generation_current(unit, grant, AdminOperation.RECOVER_CONFIG)
                for field in migration.fields:
                    grant._effect.check_resource(
                        field.target, (field.declaration.name,)
                    )
            if self._marker(unit, migration) is not None:
                return ()
            for target, snapshot in snapshots.items():
                self._revision(unit, target, snapshot.revision)
            saved = []
            for field, selection in zip(migration.fields, selected, strict=True):
                snapshot = snapshots[field.target]
                if any(
                    m.field == field.declaration.name for m in snapshot.secret_metadata
                ):
                    raise ValueError("ordinary migration contains secret metadata")
                before = (
                    {"value": snapshot.values[field.declaration.name]}
                    if field.declaration.name in snapshot.values
                    else {}
                )
                saved.append(
                    {
                        "module_id": field.target.module_id,
                        "field": field.declaration.name,
                        "before": before,
                        "after": {
                            "value": selection.value,
                            "revision": snapshot.revision + 1,
                        },
                    }
                )
                self._write(
                    unit,
                    field.target,
                    field.declaration.name,
                    selection.value,
                    snapshot.revision + 1,
                )
            for target, snapshot in snapshots.items():
                unit.execute(
                    "UPDATE config_state SET revision=? "
                    "WHERE principal_id=? AND module_id=?",
                    (snapshot.revision + 1, target.principal_id, target.module_id),
                )
            unit.execute(
                "INSERT INTO ordinary_config_migrations "
                "(principal_id,migration_id,version,snapshot_json) VALUES(?,?,?,?)",
                (
                    *self._key(migration),
                    migration.version,
                    _json({"fields": saved, "legacy": legacy}),
                ),
            )
            return tuple(selection.diagnostic() for selection in selected)

        return await self.config.database.executor.run_transaction(
            apply, begin_mode="IMMEDIATE"
        )

    async def rollback(self, migration, expected_revisions, *, grant=None):
        targets = {f.target for f in migration.fields}
        if (
            not isinstance(expected_revisions, Mapping)
            or set(expected_revisions) != targets
            or any(type(r) is not int or r < 1 for r in expected_revisions.values())
        ):
            raise ValueError("rollback requires both current target revisions")

        def restore(unit):
            if grant is not None:
                assert_generation_current(unit, grant, AdminOperation.ROLLBACK_CONFIG)
                for field in migration.fields:
                    grant._effect.check_resource(
                        field.target, (field.declaration.name,)
                    )
            marker = self._marker(unit, migration)
            if marker is None:
                raise ValueError("migration is not complete")
            for target, revision in expected_revisions.items():
                self._revision(unit, target, revision)
            for field, item in zip(migration.fields, marker["fields"], strict=True):
                row = unit.execute(
                    "SELECT value_json,revision FROM config_entries "
                    "WHERE principal_id=? AND module_id=? AND field=?",
                    (
                        field.target.principal_id,
                        field.target.module_id,
                        field.declaration.name,
                    ),
                ).fetchone()
                if (
                    row is None
                    or row[0] is None
                    or row[1] != item["after"]["revision"]
                    or _load_json(row[0], object_required=False)
                    != item["after"]["value"]
                ):
                    raise ValueError("migration field changed after completion")
            for field, item in zip(migration.fields, marker["fields"], strict=True):
                if "value" in item["before"]:
                    self._write(
                        unit,
                        field.target,
                        field.declaration.name,
                        item["before"]["value"],
                        expected_revisions[field.target] + 1,
                    )
                else:
                    unit.execute(
                        "DELETE FROM config_entries WHERE principal_id=? "
                        "AND module_id=? AND field=?",
                        (
                            field.target.principal_id,
                            field.target.module_id,
                            field.declaration.name,
                        ),
                    )
            for target, revision in expected_revisions.items():
                unit.execute(
                    "UPDATE config_state SET revision=? WHERE principal_id=? AND module_id=?",
                    (revision + 1, target.principal_id, target.module_id),
                )
            unit.execute(
                "DELETE FROM ordinary_config_migrations WHERE principal_id=? AND migration_id=?",
                self._key(migration),
            )
            return marker["legacy"]

        return await self.config.database.executor.run_transaction(
            restore, begin_mode="IMMEDIATE"
        )

    async def complete_from_current(self, migration, expected_revisions, grant):
        targets = {field.target for field in migration.fields}
        if set(expected_revisions) != targets or any(
            type(r) is not int or r < 1 for r in expected_revisions.values()
        ):
            raise ValueError("recovery requires both target revisions")

        def complete(unit):
            assert_generation_current(unit, grant, AdminOperation.RECOVER_CONFIG)
            if self._marker(unit, migration) is not None:
                raise ValueError("migration is already complete")
            for target, revision in expected_revisions.items():
                self._revision(unit, target, revision)
            saved = []
            for field in migration.fields:
                grant._effect.check_resource(field.target, (field.declaration.name,))
                row = unit.execute(
                    "SELECT value_json FROM config_entries WHERE principal_id=? AND module_id=? AND field=?",
                    (
                        field.target.principal_id,
                        field.target.module_id,
                        field.declaration.name,
                    ),
                ).fetchone()
                if row is None or row[0] is None:
                    raise ValueError(
                        "explicit replacement requires all new ordinary values"
                    )
                value = _load_json(row[0], object_required=False)
                validate_configuration_value(field.declaration, value, field.validator)
                revision = expected_revisions[field.target] + 1
                saved.append(
                    {
                        "module_id": field.target.module_id,
                        "field": field.declaration.name,
                        "before": {"value": value},
                        "after": {"value": value, "revision": revision},
                    }
                )
                self._write(unit, field.target, field.declaration.name, value, revision)
            for target, revision in expected_revisions.items():
                unit.execute(
                    "UPDATE config_state SET revision=? WHERE principal_id=? AND module_id=?",
                    (revision + 1, target.principal_id, target.module_id),
                )
            unit.execute(
                "INSERT INTO ordinary_config_migrations (principal_id,migration_id,version,snapshot_json) VALUES(?,?,?,?)",
                (
                    *self._key(migration),
                    migration.version,
                    _json(
                        {"fields": saved, "legacy": None, "source": "admin_replacement"}
                    ),
                ),
            )
            return True

        return await self.config.database.executor.run_transaction(
            complete, begin_mode="IMMEDIATE"
        )
