"""Copy AstrBot's injected config into the reviewed bundled module snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ...api.services import ConfigTarget
from ...modules.ff14.config import (
    CALENDAR_CONFIG_FIELDS,
    CALENDAR_VALUE_VALIDATORS,
    ORDINARY_FIELDS,
    REGION,
    FF14ConfigError,
    FF14ConfigSnapshot,
)
from ...services.configuration_migration import OrdinaryMigrationField
from ...services.core_configuration import DEFAULT_REGION, core_config_target


def ordinary_migration_fields(principal_id: str):
    """Host owns the legacy key map; Core owns only reserved default_region."""
    return (OrdinaryMigrationField(core_config_target(principal_id), DEFAULT_REGION, REGION),
            *(OrdinaryMigrationField(ConfigTarget(principal_id, "ff14/ff14"), field,
                                     field.name, CALENDAR_VALUE_VALIDATORS.get(field.name))
              for field in CALENDAR_CONFIG_FIELDS))


def legacy_core_defaults(config: Mapping[str, object] | None) -> Mapping[str, object]:
    """Map the raw legacy key at the Host boundary, preserving actual presence."""
    if isinstance(config, Mapping) and REGION in config:
        return MappingProxyType({"default_region": config[REGION]})
    return MappingProxyType({})


def ff14_config_snapshot(config: Mapping[str, object] | None) -> FF14ConfigSnapshot:
    """Accept missing legacy fields; reject invalid or undeclared host inputs."""
    if config is None:
        config = {}
    if isinstance(config, Mapping):
        config = {
            key: value for key, value in config.items() if key != "web_public_origin"
        }
    if not isinstance(config, Mapping) or set(config) - ORDINARY_FIELDS:
        raise FF14ConfigError("ordinary_config")
    return FF14ConfigSnapshot.from_values(config)


__all__ = ["ff14_config_snapshot"]
