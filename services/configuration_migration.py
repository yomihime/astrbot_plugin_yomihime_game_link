"""Trusted startup migration of a bounded set of ordinary config fields."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..api.manifests import ConfigField
from ..api.services import ConfigTarget
from .configuration import ConfigValueValidator
from .core_configuration import ConfigurationSelection, select_configuration_value


@dataclass(frozen=True, slots=True)
class OrdinaryMigrationField:
    target: ConfigTarget
    declaration: ConfigField
    legacy_field: str
    validator: ConfigValueValidator | None = None

    def __post_init__(self):
        ConfigTarget.validate(self.target)
        if not isinstance(self.declaration, ConfigField) or self.declaration.sensitive:
            raise ValueError("migration requires ordinary declarations")
        if type(self.legacy_field) is not str or not self.legacy_field:
            raise ValueError("migration requires a legacy field")
        if self.validator is not None and not callable(self.validator):
            raise TypeError("migration validator must be callable")


class OrdinaryConfigurationMigration:
    """No implicit raw source, no default injection, no secret/subscription writes."""

    def __init__(self, repository, fields, *, migration_id: str, version: int = 1):
        self.repository = repository
        self.fields = tuple(fields)
        if (
            not self.fields
            or any(
                not isinstance(field, OrdinaryMigrationField) for field in self.fields
            )
            or len({(f.target, f.declaration.name) for f in self.fields})
            != len(self.fields)
            or len({f.target.principal_id for f in self.fields}) != 1
            or type(migration_id) is not str
            or not migration_id
            or type(version) is not int
            or version < 1
        ):
            raise ValueError("invalid ordinary migration definition")
        self.migration_id = migration_id
        self.version = version

    async def complete(self) -> bool:
        return await self.repository.complete(self)

    async def migrate(self, raw_legacy: Mapping[str, object] | None, *, grant=None):
        if await self.complete():
            return ()
        if not isinstance(raw_legacy, Mapping):
            raise ValueError("raw ordinary configuration source is unavailable")
        if set(raw_legacy) - {f.legacy_field for f in self.fields}:
            raise ValueError("raw migration source contains unrelated fields")
        snapshots = {
            target: await self.repository.config.current(target)
            for target in dict.fromkeys(f.target for f in self.fields)
        }
        selected: tuple[ConfigurationSelection, ...] = tuple(
            select_configuration_value(
                field.declaration,
                snapshots[field.target].values,
                raw_legacy,
                legacy_field=field.legacy_field,
                validator=field.validator,
            )
            for field in self.fields
        )
        if grant is None:
            return await self.repository.apply(self, snapshots, selected, raw_legacy)
        return await self.repository.apply(
            self, snapshots, selected, raw_legacy, grant=grant
        )

    async def rollback(
        self, expected_revisions: Mapping[ConfigTarget, int], *, grant=None
    ):
        return await self.repository.rollback(self, expected_revisions, grant=grant)

    async def complete_from_current(self, expected_revisions, *, grant):
        """Explicit replacement recovery, without manufacturing legacy inputs."""
        return await self.repository.complete_from_current(
            self, expected_revisions, grant
        )
