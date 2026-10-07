"""Prepare immutable ordinary-field input before installing the C1 package.

Run only against an explicitly chosen old plugin JSON while the instance is
stopped. This helper neither edits that source nor opens the Core database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

PLUGIN_ID = "astrbot_plugin_yomihime_game_link"
INPUT_FILENAME = "ff14-config-migration-v1.json"


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse(raw: bytes):
    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("nonfinite JSON")
            ),
        )
    except (UnicodeError, ValueError):
        raise ValueError("invalid migration JSON") from None


def _canonical(fields):
    return json.dumps(
        fields,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def reviewed_legacy_fields():
    """Ordinary catalog from the fixed reviewed release; no executable imports.

    This offline selection exports presence only and grants no Core authority.
    Runtime supplies its already selected immutable fields instead of using it.
    """
    root = Path(__file__).resolve().parents[1] / "modules/ff14"
    raw = (root / "assembly.json").read_bytes()
    manifest_raw = (root / "yomihime.manifest.json").read_bytes()
    if len(raw) > 32768 or len(manifest_raw) > 262144:
        raise ValueError("reviewed preparation catalog exceeds limit")
    descriptor, manifest = _parse(raw), _parse(manifest_raw)
    if (
        type(descriptor) is not dict
        or type(descriptor.get("version")) is not int
        or descriptor["version"] != 1
        or descriptor.get("module_id") != "ff14/ff14"
        or type(manifest) is not dict
        or manifest.get("package_id") != "ff14"
    ):
        raise ValueError("reviewed preparation catalog is invalid")
    modules = manifest.get("modules")
    if type(modules) is not list:
        raise ValueError("reviewed preparation manifest is invalid")
    selected = [
        item
        for item in modules
        if type(item) is dict and item.get("module_id") == "ff14"
    ]
    if len(selected) != 1:
        raise ValueError("reviewed preparation module is unavailable")
    declarations = {item["name"]: item for item in selected[0].get("config_fields", ())}
    migration = descriptor.get("migration")
    if type(migration) is not dict or set(migration) != {"id", "fields"}:
        raise ValueError("reviewed preparation migration is invalid")
    fields = migration["fields"]
    if type(fields) is not list or not 1 <= len(fields) <= 32:
        raise ValueError("reviewed preparation field limit is invalid")
    legacy = set()
    for item in fields:
        if type(item) is not dict or set(item) != {"target", "field", "legacy"}:
            raise ValueError("reviewed preparation field is invalid")
        name = item["legacy"]
        if (
            type(name) is not str
            or re.fullmatch(r"[a-z][a-z0-9_.]{0,127}", name) is None
            or name in legacy
        ):
            raise ValueError("reviewed preparation legacy key is invalid")
        if item["target"] == "$core":
            if item["field"] != "default_region":
                raise ValueError("reviewed preparation Core reference is invalid")
        elif item["target"] == "ff14/ff14":
            field = declarations.get(item["field"])
            if (
                field is None
                or field.get("sensitive", False) is not False
                or item["field"] == descriptor.get("gate")
            ):
                raise ValueError("reviewed preparation requires ordinary fields")
        else:
            raise ValueError("reviewed preparation target is invalid")
        legacy.add(name)
    return frozenset(legacy)


def prepare(source: Path, output: Path):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or output.exists():
        raise ValueError("migration input must be a new file distinct from its source")
    raw = source.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("legacy plugin JSON exceeds preparation limit")
    original = _parse(raw)
    if not isinstance(original, dict):
        raise ValueError("legacy plugin JSON must be an object")
    allowed = reviewed_legacy_fields()
    fields = {key: value for key, value in original.items() if key in allowed}
    payload = {
        "schema_version": 1,
        "plugin_id": PLUGIN_ID,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "fields_sha256": hashlib.sha256(_canonical(fields)).hexdigest(),
        "fields": fields,
    }
    encoded = _canonical(payload)
    if len(encoded) > 16384:
        raise ValueError("ordinary migration input exceeds limit")
    with output.open("xb") as handle:
        handle.write(encoded + b"\n")
    return payload


def load_prepared_input(path: Path, *, legacy_fields=None):
    allowed = (
        reviewed_legacy_fields() if legacy_fields is None else frozenset(legacy_fields)
    )
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        raise ValueError(
            "prepared migration input required: stop the instance and run scripts/prepare_ff14_config_migration.py"
        ) from None
    if len(raw) > 16385:
        raise ValueError("prepared migration input exceeds limit")
    try:
        payload = _parse(raw)
    except ValueError:
        raise ValueError(
            "prepared migration input is incomplete or invalid; preserve the original JSON and prepare a new snapshot before retry"
        ) from None
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"schema_version", "plugin_id", "source_sha256", "fields_sha256", "fields"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["plugin_id"] != PLUGIN_ID
        or not isinstance(payload["fields"], dict)
        or set(payload["fields"]) - allowed
        or any(
            type(payload[key]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", payload[key]) is None
            for key in ("source_sha256", "fields_sha256")
        )
        or hashlib.sha256(_canonical(payload["fields"])).hexdigest()
        != payload["fields_sha256"]
    ):
        raise ValueError("prepared migration input binding or digest is invalid")
    return payload["fields"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=f"new file, normally plugin data directory/{INPUT_FILENAME}",
    )
    args = parser.parse_args()
    try:
        prepare(args.source, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Migration preparation failed: {exc}\n")
    print(
        "Prepared ordinary-field migration input. Original JSON and Core database are unchanged."
    )


if __name__ == "__main__":
    main()
