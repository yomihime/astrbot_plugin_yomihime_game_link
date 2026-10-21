"""Trusted, closed ordinary-field migration policy; never an SDK read port."""

from __future__ import annotations

import re
from dataclasses import dataclass

from yomihime_game_link_sdk import (
    ConfigField,
    ConfigSnapshot,
    ConfigTarget,
    RevisionConflict,
    ServiceUnavailable,
)

from ...core.contracts.schema import freeze_input_schema
from ...core.contracts.validation_boundary import validate_contract
from .operations import validate_configuration_value


@dataclass(frozen=True, slots=True)
class LegacyConfigCopyPolicy:
    policy_id: str
    source_target: ConfigTarget
    source_field: str
    target_owner: str
    target_field: str
    target_schema: object

    def __post_init__(self):
        if (
            type(self.policy_id) is not str
            or re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", self.policy_id) is None
        ):
            raise ValueError("invalid legacy policy ID")
        validate_contract(self.source_target)
        validate_contract(
            ConfigTarget(self.source_target.principal_id, self.target_owner)
        )
        for name in (self.source_field, self.target_field):
            if (
                type(name) is not str
                or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", name) is None
                or re.search(r"secret|token|password|credential|key", name, re.I)
            ):
                raise ValueError("legacy policy requires an ordinary field")
        object.__setattr__(
            self, "target_schema", freeze_input_schema(self.target_schema)
        )

    @property
    def target(self):
        return ConfigTarget(self.source_target.principal_id, self.target_owner)

    def validate(self, source, target, declaration):
        """Return status + frozen source value, only inside trusted code."""
        for snapshot, expected in ((source, self.source_target), (target, self.target)):
            validate_contract(snapshot)
            if type(snapshot) is not ConfigSnapshot or snapshot.target not in (
                None,
                expected,
            ):
                raise ValueError("legacy snapshot target mismatch")
        validate_contract(declaration)
        if (
            type(declaration) is not ConfigField
            or declaration.name != self.target_field
            or declaration.sensitive
            or declaration.value_schema != self.target_schema
        ):
            raise ValueError("legacy target declaration changed")
        if any(
            meta.field == self.source_field for meta in source.secret_metadata
        ) or any(meta.field == self.target_field for meta in target.secret_metadata):
            raise ValueError("legacy secret metadata rejected")
        if self.target_field in target.values:
            return "already_configured", None
        if self.source_field not in source.values:
            return "not_applicable", None
        value = source.values[self.source_field]
        try:
            validate_configuration_value(declaration, value)
        except (ValueError, TypeError):
            raise ValueError("invalid_legacy_value") from None
        return "migration_pending", value


async def effective_configuration(repository, snapshot, policies, fields):
    """Use the target revision with safe destination-only effective values.

    Caller owns its shared admission mutation and actual authority. Each legacy
    source/target pair is one repository snapshot; changed target revisions fail
    instead of mixing a stale target with a newer source.
    """
    declarations = {field.name: field for field in fields}
    values = dict(snapshot.values)
    origins = {}
    for policy in policies:
        if policy.target != snapshot.target or policy.target_field not in declarations:
            continue
        if policy.target_field in values:
            continue
        read_pair = getattr(repository, "current_pair", None)
        if not callable(read_pair):
            raise ServiceUnavailable()
        source, target = await read_pair(policy.source_target, policy.target)
        if target.revision != snapshot.revision:
            raise RevisionConflict("config", snapshot.revision, target.revision)
        status, value = policy.validate(
            source, target, declarations[policy.target_field]
        )
        if status == "migration_pending":
            values[policy.target_field] = value
            origins[policy.target_field] = "legacy"
    return ConfigSnapshot(
        snapshot.revision, values, snapshot.secret_metadata, snapshot.target
    ), origins


class LegacyConfigProjection:
    """Project only reviewed destination fields under the shared mutation gate."""

    __slots__ = ("_config", "_repository", "_policies", "_admission", "_fields")

    def __init__(self, config, repository, policies, admission, fields):
        self._config = config
        self._repository = repository
        self._policies = tuple(policies)
        self._admission = admission
        self._fields = {field.name: field for field in fields}

    async def current(self):
        async with self._admission.mutation("legacy-config-projection"):
            result = await self._config.current()
            effective, _ = await effective_configuration(
                self._repository, result, self._policies, self._fields.values()
            )
            return effective
