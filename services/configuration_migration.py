"""Trusted startup migration of a bounded set of ordinary config fields."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from yomihime_game_link_sdk.declarations import ConfigField
from yomihime_game_link_sdk.services import ConfigTarget

from ..core.contracts.services import ConfigTarget_validate
from ..core.contracts.validation_boundary import validate_contract
from .configuration import ConfigValueValidator
from .core_configuration import ConfigurationSelection, select_configuration_value


@dataclass(frozen=True, slots=True)
class OrdinaryMigrationField:
    target: ConfigTarget
    declaration: ConfigField
    legacy_field: str
    validator: ConfigValueValidator | None = None

    def __post_init__(self):
        ConfigTarget_validate(self.target)
        if not isinstance(self.declaration, ConfigField) or self.declaration.sensitive:
            raise ValueError("migration requires ordinary declarations")
        if type(self.legacy_field) is not str or not self.legacy_field:
            raise ValueError("migration requires a legacy field")
        if self.validator is not None and not callable(self.validator):
            raise TypeError("migration validator must be callable")


class OrdinaryConfigurationMigration:
    """No implicit raw source, no default injection, no secret/subscription writes."""

    def __init__(
        self,
        repository,
        fields,
        *,
        migration_id: str,
        version: int = 1,
        value_validators: Mapping[str, Mapping[str, object]] | None = None,
    ):
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
        # The trusted migration binding freezes required semantics, independently
        # of the declaration catalog. Missing live bindings never downgrade them.
        self._required_validators = MappingProxyType(
            {
                (field.target, field.declaration.name): field.validator
                for field in self.fields
                if field.validator is not None
            }
        )
        if value_validators is None:
            checks = {}
            for (target, name), validator in self._required_validators.items():
                checks.setdefault(target.module_id, {})[name] = validator
        else:
            checks = value_validators
        self._value_validators = MappingProxyType(
            {
                module_id: MappingProxyType(dict(values))
                for module_id, values in checks.items()
            }
        )

    def require_semantic_validators(self, resources=None, *, value_validators=None):
        checks = (
            self._value_validators if value_validators is None else value_validators
        )
        for (target, name), expected in self._required_validators.items():
            if resources is not None and name not in resources.get(target, ()):
                continue
            actual = checks.get(target.module_id, {}).get(name)
            if not callable(actual) or actual is not expected:
                raise ValueError("semantic validator unavailable")

    async def complete(self) -> bool:
        return await self.repository.complete(self)

    async def migrate(self, raw_legacy: Mapping[str, object] | None, *, grant=None):
        self.require_semantic_validators()
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
        validate_contract(expected_revisions)
        return await self.repository.rollback(self, expected_revisions, grant=grant)

    async def complete_from_current(self, expected_revisions, *, grant):
        """Explicit replacement recovery, without manufacturing legacy inputs."""
        self.require_semantic_validators()
        return await self.repository.complete_from_current(
            self, expected_revisions, grant
        )
