"""Explicit test-only storage corruption; never a production write interface."""

import json
from types import SimpleNamespace


def field_binding(target, declaration, validator=None):
    return SimpleNamespace(target=target, declaration=declaration, validator=validator)


def write_value(unit, target, field, value, revision):
    unit.execute(
        "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES(?,?,?,?,?) "
        "ON CONFLICT(principal_id,module_id,field) DO UPDATE SET value_json=excluded.value_json,revision=excluded.revision",
        (target.principal_id, target.module_id, field, json.dumps(value), revision),
    )
