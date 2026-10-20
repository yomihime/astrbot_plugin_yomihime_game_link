"""Validated current Core defaults and per-module presentation preferences."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from yomihime_game_link_sdk.declarations import ConfigField
from yomihime_game_link_sdk.services import ConfigSnapshot, ConfigTarget

from ...core.contracts.validation_boundary import validate_contract
from ...presentation.layout_renderer import list_themes
from .operations import validate_configuration_value

CORE_MODULE_ID = "game_link/core"
CORE_DEFAULTS_FIELD = "core_defaults"
_THEME_IDS = tuple(option.id for option in list_themes())
IMAGE_THEME = validate_contract(
    ConfigField(
        "image_theme",
        default="warm_paper",
        description="图片主题",
        group="reply_style",
        value_schema={"type": "string", "enum": _THEME_IDS},
    )
)
MODULE_REPLY_THEME = validate_contract(
    ConfigField(
        "reply_theme",
        default=None,
        description="图片主题",
        group="reply_style",
        value_schema={"type": "string", "enum": _THEME_IDS},
    )
)
CORE_CONFIG_FIELDS = (IMAGE_THEME,)


def module_configuration_fields(
    fields: Iterable[ConfigField],
) -> tuple[ConfigField, ...]:
    """Attach Core's presentation preference without changing a module manifest.

    The nullable view is represented by an absent override in ordinary storage.
    CLEAR restores follow-Core; explicit null and unknown IDs are invalid writes.
    """
    declared = tuple(fields)
    validate_contract(declared)
    reserved = {
        CORE_DEFAULTS_FIELD,
        *(field.name for field in CORE_CONFIG_FIELDS),
        MODULE_REPLY_THEME.name,
    }
    if any(field.name in reserved for field in declared):
        raise ValueError("module config field is reserved by Core")
    return (*declared, MODULE_REPLY_THEME)


def presentation_choices(module_id: str, field: ConfigField):
    """Read-only labels from the exact renderer catalog, never a UI palette copy."""
    expected = IMAGE_THEME if module_id == CORE_MODULE_ID else MODULE_REPLY_THEME
    if field != expected:
        return None
    choices = [{"value": option.id, "label": option.label} for option in list_themes()]
    if module_id != CORE_MODULE_ID:
        choices.insert(0, {"value": None, "label": "跟随核心"})
    return choices


@dataclass(frozen=True, slots=True)
class ThemeSettingsSnapshot:
    core_theme: str
    module_theme: str | None
    core_revision: int
    module_revision: int | None


class ThemeSettingsView:
    """Read one coherent Core/module policy under the shared mutation gate."""

    def __init__(self, repository, admission, *, principal_id: str):
        self._repository = repository
        self._admission = admission
        self._core_target = core_config_target(principal_id)

    async def current(self, module_id: str | None = None) -> ThemeSettingsSnapshot:
        target = (
            validate_contract(ConfigTarget(self._core_target.principal_id, module_id))
            if module_id is not None and module_id != CORE_MODULE_ID
            else None
        )
        async with self._admission.mutation("theme-settings-read"):
            core = await self._read(self._core_target, IMAGE_THEME)
            core_theme = core.values.get(IMAGE_THEME.name, IMAGE_THEME.default)
            validate_configuration_value(IMAGE_THEME, core_theme)
            module_theme, module_revision = None, None
            if target is not None:
                module = await self._read(target, MODULE_REPLY_THEME)
                module_revision = module.revision
                if MODULE_REPLY_THEME.name in module.values:
                    module_theme = module.values[MODULE_REPLY_THEME.name]
                    validate_configuration_value(MODULE_REPLY_THEME, module_theme)
            return ThemeSettingsSnapshot(
                core_theme, module_theme, core.revision, module_revision
            )

    async def _read(self, target, field):
        snapshot = validate_contract(await self._repository.current(target))
        if not isinstance(snapshot, ConfigSnapshot) or snapshot.target not in (
            None,
            target,
        ):
            raise ValueError("theme configuration snapshot target mismatch")
        if any(item.field == field.name for item in snapshot.secret_metadata):
            raise ValueError("theme configuration contains sensitive metadata")
        return snapshot


def core_config_target(principal_id: str) -> ConfigTarget:
    return validate_contract(ConfigTarget(principal_id, CORE_MODULE_ID))
