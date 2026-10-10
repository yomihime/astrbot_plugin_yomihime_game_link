"""Reserved Core defaults; raw presence is resolved before any default injection.

This establishes the C0 read/selection contract. It performs no migration writes
and contains no game's region directory or Host-specific legacy field names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from yomihime_game_link_sdk.declarations import ConfigField
from yomihime_game_link_sdk.services import ConfigSnapshot, ConfigTarget

from ..core.contracts.storage import freeze_json
from ..core.contracts.validation_boundary import validate_contract
from .configuration import ConfigValueValidator, validate_configuration_value

CORE_MODULE_ID = "game_link/core"
CORE_DEFAULTS_FIELD = "core_defaults"
DEFAULT_REGION = validate_contract(
    ConfigField(
        "default_region",
        default="cn",
        description="默认查询区域",
        value_schema={"type": "string", "enum": ["cn", "global"]},
    )
)
CORE_CONFIG_FIELDS = (DEFAULT_REGION,)


@dataclass(frozen=True, slots=True)
class ConfigurationSelection:
    field: str
    value: object
    origin: str
    conflict: bool
    new_present: bool
    legacy_present: bool

    def __post_init__(self):
        object.__setattr__(self, "value", freeze_json(self.value))

    def diagnostic(self) -> Mapping[str, object]:
        """Safe conflict/origin record; raw values stay outside diagnostics."""
        return MappingProxyType(
            {
                "field": self.field,
                "origin": self.origin,
                "conflict": self.conflict,
                "new_present": self.new_present,
                "legacy_present": self.legacy_present,
            }
        )


@dataclass(frozen=True, slots=True)
class ConfigurationRollbackPlan:
    """Detached ordinary-field recovery inputs; never executes a rollback."""

    original_new_values: Mapping[str, object]
    original_legacy_values: Mapping[str, object]
    requires_current_export: bool


def plan_configuration_rollback(
    fields: Iterable[ConfigField],
    original_new_values: Mapping[str, object],
    original_legacy_values: Mapping[str, object],
    *,
    new_configuration_written: bool,
) -> ConfigurationRollbackPlan:
    """Preserve missing and invalid raw data instead of synthesizing defaults.

    After any new-authority write the caller must export current defaults before
    restoring the legacy package; this plan never proposes a whole-database restore.
    """
    validate_contract(fields)
    declarations = tuple(fields)
    if not declarations or any(
        not isinstance(item, ConfigField) or item.sensitive for item in declarations
    ):
        raise ValueError("rollback planning requires ordinary declarations")
    if type(new_configuration_written) is not bool:
        raise TypeError("rollback write marker must be bool")
    names = {item.name for item in declarations}
    for values in (original_new_values, original_legacy_values):
        if not isinstance(values, Mapping) or set(values) - names:
            raise ValueError("rollback inputs require declared ordinary fields")
    return ConfigurationRollbackPlan(
        freeze_json(original_new_values),
        freeze_json(original_legacy_values),
        new_configuration_written,
    )


def select_configuration_value(
    field: ConfigField,
    new_values: Mapping[str, object],
    legacy_values: Mapping[str, object],
    *,
    legacy_field: str | None = None,
    validator: ConfigValueValidator | None = None,
) -> ConfigurationSelection:
    """Pure selection: valid explicit new > valid legacy > declaration default.

    Explicit invalid new data always rejects. Invalid legacy data only rejects when
    there is no valid new value. Neither input is altered or erased for rollback.
    """
    validate_contract(field)
    if field.sensitive:
        raise ValueError("selection requires an ordinary config declaration")
    if not isinstance(new_values, Mapping) or not isinstance(legacy_values, Mapping):
        raise TypeError("configuration selection requires mappings")
    old_name = legacy_field or field.name
    present = field.name in new_values
    old_present = old_name in legacy_values
    conflict = False
    if present:
        value = new_values[field.name]
        validate_configuration_value(field, value, validator)
        if old_present:
            try:
                validate_configuration_value(field, legacy_values[old_name], validator)
            except ValueError:
                pass
            else:
                conflict = value != legacy_values[old_name]
        origin = "new"
    elif old_present:
        value = legacy_values[old_name]
        validate_configuration_value(field, value, validator)
        origin = "legacy"
    else:
        value = field.default
        validate_configuration_value(field, value, validator)
        origin = "default"
    return ConfigurationSelection(
        field.name, value, origin, conflict, present, old_present
    )


class CoreDefaultsView:
    """Read-only view of typed defaults plus the reserved target's own revision."""

    __slots__ = ("__current",)

    def __init__(
        self,
        configuration,
        *,
        legacy_defaults: Mapping[str, object] | None = None,
        migration_complete=None,
    ):
        if legacy_defaults is None:
            legacy_defaults = {}
        if not isinstance(legacy_defaults, Mapping):
            raise TypeError("legacy Core defaults require a mapping")
        legacy = validate_contract(ConfigSnapshot(1, legacy_defaults)).values
        if set(legacy) - {"default_region"}:
            raise ValueError("legacy Core defaults contain an undeclared field")

        async def current():
            raw = await configuration.current()
            old = (
                {}
                if migration_complete is not None and await migration_complete()
                else legacy
            )
            selected = select_configuration_value(DEFAULT_REGION, raw.values, old)
            return validate_contract(
                ConfigSnapshot(
                    raw.revision,
                    {
                        "default_region": selected.value,
                        "origin": selected.origin,
                        "conflict": selected.conflict,
                        "raw_present": selected.new_present,
                    },
                    target=raw.target,
                )
            )

        object.__setattr__(self, "_CoreDefaultsView__current", current)

    async def current(self) -> ConfigSnapshot:
        return await object.__getattribute__(self, "_CoreDefaultsView__current")()

    def __getattribute__(self, name):
        if name in {"current", "__class__", "__dir__"}:
            return object.__getattribute__(self, name)
        raise AttributeError("Core defaults expose only current")

    def __dir__(self):
        return ["current"]


class CoreDefaultsConfigView:
    """Read under the shared mutation gate; revisions remain separate."""

    __slots__ = ("__current",)

    def __init__(self, module_config, defaults: CoreDefaultsView, admission):
        async def current():
            async with admission.mutation("config-defaults-read"):
                module = await module_config.current()
                core = await defaults.current()
                if CORE_DEFAULTS_FIELD in module.values:
                    raise ValueError("module config contains a reserved field")
                return validate_contract(
                    ConfigSnapshot(
                        module.revision,
                        {
                            **module.values,
                            CORE_DEFAULTS_FIELD: {
                                **core.values,
                                "revision": core.revision,
                            },
                        },
                        module.secret_metadata,
                        module.target,
                    )
                )

        object.__setattr__(self, "_CoreDefaultsConfigView__current", current)

    async def current(self) -> ConfigSnapshot:
        return await object.__getattribute__(self, "_CoreDefaultsConfigView__current")()

    def __getattribute__(self, name):
        if name in {"current", "__class__", "__dir__"}:
            return object.__getattribute__(self, name)
        raise AttributeError("config defaults expose only current")

    def __dir__(self):
        return ["current"]


def core_config_target(principal_id: str) -> ConfigTarget:
    return validate_contract(ConfigTarget(principal_id, CORE_MODULE_ID))
