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
FIELDS = frozenset(
    (
        "ff14_default_region",
        "ff14_calendar_default_days",
        "ff14_calendar_default_timezone",
        "ff14_calendar_default_delivery_time",
    )
)


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
    fields = {key: value for key, value in original.items() if key in FIELDS}
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


def load_prepared_input(path: Path):
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
        or set(payload["fields"]) - FIELDS
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
