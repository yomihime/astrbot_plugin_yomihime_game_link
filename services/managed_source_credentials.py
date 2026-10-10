"""Trusted, default-empty management policy; source consumption grants nothing."""

from __future__ import annotations

import json
from dataclasses import dataclass

from yomihime_game_link_sdk.declarations import ConfigField

from ..core.contracts.services import SecretMaterial
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import validate_module_id
from .source_credentials import _client_field


@dataclass(frozen=True, slots=True)
class ManagedSourceCredentialPolicy:
    module_id: str
    name: str
    group: str
    description: str

    def __post_init__(self):
        validate_module_id(self.module_id, "managed credential owner")
        validate_contract(
            ConfigField(
                self.name,
                sensitive=True,
                group=self.group,
                description=self.description,
            )
        )
        if not self.group or not self.description or len(self.description) > 4096:
            raise ValueError("managed credential form is invalid")

    def validate_declaration(self, manifest):
        matches = [field for field in manifest.config_fields if field.name == self.name]
        if len(matches) != 1:
            raise ValueError("managed credential declaration is unavailable")
        field = matches[0]
        if (
            not field.sensitive
            or field.default is not None
            or field.value_schema is not None
            or field.group != self.group
        ):
            raise ValueError("managed credential declaration is invalid")

    def project(self):
        return {
            "module_id": self.module_id,
            "name": self.name,
            "group": self.group,
            "description": self.description,
            "value_schema": client_credentials_schema(),
        }

    def material(self, value):
        if type(value) is not dict or set(value) != {"client_id", "client_secret"}:
            raise ValueError("invalid client credentials pair")
        pair = {
            name: _client_field(value[name]) for name in ("client_id", "client_secret")
        }
        return SecretMaterial(
            json.dumps(
                {"schema": 1, **pair}, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )


def client_credentials_schema():
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "minLength": 1, "maxLength": 512}
            for name in ("client_id", "client_secret")
        },
        "required": ["client_id", "client_secret"],
        "additionalProperties": False,
    }
