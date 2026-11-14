"""Bounded, host-independent YGL hot-update wire protocol.

Parsing does not authenticate a host caller, verify a signature, inspect a
filesystem, or grant capabilities. Those are separate Core admission steps.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any


class ProtocolError(ValueError):
    """A malformed or unsupported protocol document."""


_SAFE_INT = 2**53 - 1
_ID = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_PRERELEASE_ID = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_HOST_VERSION = re.compile(
    _VERSION.pattern.removesuffix(r"\Z")
    + rf"(?:-{_PRERELEASE_ID}(?:\.{_PRERELEASE_ID})*)?"
    + r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_FEATURE = re.compile(r"[a-z][a-z0-9._-]{0,63}/[1-9][0-9]{0,5}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
HOST_RELEASE_SCHEMA = "ygl.release/2"
CONTROL_RETIRE_FEATURE = "control.retire/1"
_B05 = frozenset(
    {
        "schema_version",
        "package_id",
        "package_version",
        "contract_version",
        "author",
        "license",
        "source",
        "modules",
    }
)
_B05_OPTIONAL = frozenset({"display_name", "module_order"})


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ProtocolError("duplicate JSON key")
        result[key] = value
    return result


def _bad_constant(_: str) -> None:
    raise ProtocolError("non-finite JSON number")


def _budget(value: Any, *, depth_limit: int, node_limit: int) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > node_limit or depth > depth_limit:
            raise ProtocolError("JSON complexity budget exceeded")
        if type(item) is int and not -_SAFE_INT <= item <= _SAFE_INT:
            raise ProtocolError("unsafe JSON integer")
        if type(item) is float and not math.isfinite(item):
            raise ProtocolError("non-finite JSON number")
        if type(item) is str:
            if len(item) > 8192:
                raise ProtocolError("JSON string budget exceeded")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ProtocolError("invalid Unicode scalar") from exc
        if type(item) is list:
            if len(item) > node_limit:
                raise ProtocolError("JSON array budget exceeded")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            if len(item) > node_limit:
                raise ProtocolError("JSON object budget exceeded")
            for key, child in item.items():
                if len(key) > 256:
                    raise ProtocolError("JSON key budget exceeded")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ProtocolError("invalid Unicode scalar") from exc
                pending.append((child, depth + 1))


def _load(
    data: bytes, limit: int, *, depth: int = 32, nodes: int = 20000
) -> dict[str, Any]:
    if type(data) is not bytes or len(data) > limit:
        raise ProtocolError("invalid JSON byte input or size")
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant
        )
    except ProtocolError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise ProtocolError("invalid UTF-8 JSON") from exc
    _budget(value, depth_limit=depth, node_limit=nodes)
    if type(value) is not dict:
        raise ProtocolError("JSON root must be an object")
    return value


def _fields(
    value: Any,
    required: set[str] | frozenset[str],
    optional: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or not required <= value.keys()
        or value.keys() - required - optional
    ):
        raise ProtocolError("unknown or missing fields")
    return value


def _string(value: Any, *, limit: int = 128, nonempty: bool = True) -> str:
    if type(value) is not str or len(value) > limit or (nonempty and not value):
        raise ProtocolError("invalid string")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ProtocolError("control character in string")
    return value


def _integer(value: Any, *, minimum: int = 0, maximum: int = _SAFE_INT) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError("invalid integer")
    return value


def _match(value: Any, pattern: re.Pattern[str]) -> str:
    value = _string(value)
    if not pattern.fullmatch(value):
        raise ProtocolError("invalid identifier")
    return value


def _package_id(value: Any) -> str:
    value = _match(value, _ID)
    if len(value) > 64:
        raise ProtocolError("package_id too long")
    return value


def _plugin_id(value: Any) -> dict[str, str]:
    item = _fields(value, {"author", "name"})
    return {key: _string(item[key], limit=128) for key in ("author", "name")}


def _path(value: Any) -> str:
    path = _string(value, limit=1024)
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProtocolError("invalid Unicode scalar") from exc
    if (
        len(encoded) > 1024
        or path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(part in ("", ".", "..") for part in path.split("/"))
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in path)
    ):
        raise ProtocolError("unsafe relative POSIX path")
    return path


def _features(value: Any) -> list[str]:
    if type(value) is not list or len(value) > 256:
        raise ProtocolError("invalid feature list")
    result = [_match(item, _FEATURE) for item in value]
    if len(result) != len(set(result)):
        raise ProtocolError("duplicate feature")
    return result


def parse_plugin_descriptor(data: bytes) -> dict[str, Any]:
    doc = _load(data, 16384, depth=8, nodes=256)
    _fields(doc, {"schema", "role", "package_id", "manifest", "control_protocol"})
    if doc["schema"] != "ygl.plugin/1" or doc["role"] not in ("core", "dlc"):
        raise ProtocolError("unsupported plugin descriptor")
    _package_id(doc["package_id"])
    _path(doc["manifest"])
    if doc["role"] == "core":
        if doc["package_id"] != "ygl_core" or doc["manifest"] != "release.json":
            raise ProtocolError("invalid Core release descriptor")
    elif doc["manifest"].casefold() in {"release.json", "release.sig.json"}:
        raise ProtocolError("DLC requires an inventory-covered business manifest")
    control = _fields(doc["control_protocol"], {"name", "major"})
    if control["name"] != "ygl.control" or _integer(control["major"]) != 1:
        raise ProtocolError("unsupported control protocol")
    return doc


def parse_package_manifest(
    data: bytes, *, host_install: bool = False
) -> dict[str, Any]:
    """Validate v1 envelope. Caller must also validate ``b05_manifest_bytes`` with B05 parse_manifest."""
    doc = _load(data, 1048576, depth=32, nodes=20000)
    _fields(
        doc,
        _B05
        | {
            "release",
            "module_contract",
            "required_features",
            "optional_features",
            "data",
        },
        _B05_OPTIONAL | {"metadata", "source_policy"},
    )
    if type(doc["schema_version"]) is not int or doc["schema_version"] != 1:
        raise ProtocolError("unsupported manifest schema")
    _package_id(doc["package_id"])
    for field in ("release", "package_version"):
        _match(doc[field], _HOST_VERSION if host_install else _VERSION)
    if doc["release"] != doc["package_version"] or doc["contract_version"] != "2.0":
        raise ProtocolError("inconsistent manifest versions")
    for field in ("author", "license", "source"):
        _string(doc[field], limit=2048)
    contract = _fields(doc["module_contract"], {"major", "minimum_minor"})
    if _integer(contract["major"]) != 2:
        raise ProtocolError("unsupported module contract major")
    _integer(contract["minimum_minor"], maximum=65535)
    required = _features(doc["required_features"])
    optional = _features(doc["optional_features"])
    if set(required) & set(optional):
        raise ProtocolError("feature listed twice")
    if "source_policy" in doc:
        _path(doc["source_policy"])
        if "source.policies/1" not in required:
            raise ProtocolError("source_policy requires source.policies/1")
    data_contract = _fields(doc["data"], {"schema", "readable", "writes"})
    _integer(data_contract["schema"], minimum=1)
    _integer(data_contract["writes"], minimum=1)
    readable = data_contract["readable"]
    if type(readable) is not list or not readable or len(readable) > 256:
        raise ProtocolError("invalid readable formats")
    formats = [_integer(item, minimum=1) for item in readable]
    if len(set(formats)) != len(formats):
        raise ProtocolError("inconsistent data contract")
    if (
        type(doc["modules"]) is not list
        or not doc["modules"]
        or len(doc["modules"]) > 256
    ):
        raise ProtocolError("invalid module list")
    # Opaque B05 module declarations are intentionally checked by B05 parse_manifest.
    if "metadata" in doc:
        metadata = doc["metadata"]
        if type(metadata) is not dict or len(metadata) > 64:
            raise ProtocolError("invalid metadata bag")
        for key, value in metadata.items():
            _string(key, limit=128)
            if type(value) not in (str, bool, int, type(None)):
                raise ProtocolError("metadata values must be scalar")
    return doc


def b05_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Project the B05 input; call the existing parse_manifest on these bytes."""
    return json.dumps(
        {key: manifest[key] for key in _B05 | _B05_OPTIONAL if key in manifest},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def negotiate_manifest(
    manifest: Mapping[str, Any], supported_minor: int, features: Sequence[str]
) -> tuple[str, ...]:
    _integer(supported_minor, maximum=65535)
    if manifest["module_contract"]["minimum_minor"] > supported_minor:
        raise ProtocolError("unsupported_contract")
    supported = set(features)
    if any(feature not in supported for feature in manifest["required_features"]):
        raise ProtocolError("missing_feature")
    return tuple(
        sorted(
            set(manifest["required_features"])
            | (set(manifest["optional_features"]) & supported)
        )
    )


def check_data_compatibility(
    manifest: Mapping[str, Any],
    accepted_schema: int,
    accepted_writes: int,
    current_format: int,
) -> None:
    """V1 refuses DLC migrations; readable must include the current stored format."""
    contract = manifest["data"]
    if (
        contract["schema"] != accepted_schema
        or contract["writes"] != accepted_writes
        or current_format not in contract["readable"]
    ):
        raise ProtocolError("data_incompatible")


def payload_digest(files: Sequence[Mapping[str, Any]]) -> str:
    if type(files) is not list or not files or len(files) > 10000:
        raise ProtocolError("invalid inventory")
    by_path: dict[str, tuple[int, str]] = {}
    folded: set[str] = set()
    components: dict[str, str] = {}
    for file in files:
        item = _fields(file, {"path", "size", "sha256"})
        path = _path(item["path"])
        if (
            path.casefold() in ("release.json", "release.sig.json")
            or path.casefold() in folded
        ):
            raise ProtocolError("duplicate, colliding or recursive inventory path")
        pieces = path.split("/")
        for index in range(1, len(pieces) + 1):
            prefix = "/".join(pieces[:index])
            folded_prefix = prefix.casefold()
            prior = components.get(folded_prefix)
            if prior is not None and prior != prefix:
                raise ProtocolError("case-colliding inventory path")
            components[folded_prefix] = prefix
        folded.add(path.casefold())
        by_path[path] = (_integer(item["size"]), _match(item["sha256"], _DIGEST))
    if any(
        "/".join(path.split("/")[:index]) in by_path
        for path in by_path
        for index in range(1, len(path.split("/")))
    ):
        raise ProtocolError("file and directory inventory conflict")
    digest = hashlib.sha256()
    for path in sorted(by_path, key=lambda item: item.encode("utf-8")):
        size, sha256 = by_path[path]
        digest.update(
            path.encode("utf-8")
            + b"\0"
            + str(size).encode("ascii")
            + b"\0"
            + sha256.encode("ascii")
            + b"\n"
        )
    return digest.hexdigest()


def parse_release(data: bytes) -> dict[str, Any]:
    doc = _load(data, 2097152, depth=12, nodes=50000)
    fields = {"schema", "plugin_id", "package_id", "release", "payload_digest", "files"}
    schema = doc.get("schema")
    if schema == "ygl.release/1":
        _fields(doc, fields | {"channel", "release_sequence"})
        if doc["channel"] not in ("stable", "preview"):
            raise ProtocolError("invalid release channel")
        _integer(doc["release_sequence"])
    elif schema == HOST_RELEASE_SCHEMA:
        _fields(doc, fields)
    else:
        raise ProtocolError("unsupported release schema")
    _plugin_id(doc["plugin_id"])
    _package_id(doc["package_id"])
    _match(doc["release"], _HOST_VERSION if schema == HOST_RELEASE_SCHEMA else _VERSION)
    if _match(doc["payload_digest"], _DIGEST) != payload_digest(doc["files"]):
        raise ProtocolError("payload digest mismatch")
    if schema == HOST_RELEASE_SCHEMA and any(
        row["path"].casefold() == "release-key.json" for row in doc["files"]
    ):
        raise ProtocolError("host release contains a signing key envelope")
    return doc


def _uuid(value: Any) -> str:
    value = _string(value, limit=36)
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise ProtocolError("noncanonical UUID") from exc
    return value


def parse_control(data: bytes) -> dict[str, Any]:
    doc = _load(data, 65536, depth=16, nodes=4096)
    op = doc.get("op")
    common = {"protocol", "op"}
    optional = {
        "describe": set(),
        "connect": {"request_id", "descriptor_digest"},
        "status": {"operation_id", "lease"},
        "disconnect": {"lease"},
        "retire": set(),
    }
    if type(op) is not str or op not in optional:
        raise ProtocolError("unsupported operation")
    required = common | (
        {"request_id", "descriptor_digest"}
        if op == "connect"
        else {"lease"}
        if op == "disconnect"
        else set()
    )
    _fields(doc, required, optional[op] - required)
    if doc["protocol"] != "ygl.control/1":
        raise ProtocolError("unsupported control protocol")
    if op == "connect":
        _uuid(doc["request_id"])
        _match(doc["descriptor_digest"], _DIGEST)
    if op == "status":
        if "operation_id" not in doc and "lease" not in doc:
            raise ProtocolError("status requires operation_id or lease")
        if "operation_id" in doc:
            _string(doc["operation_id"], limit=128)
    if op in ("status", "disconnect") and "lease" in doc:
        _match(doc["lease"], _DIGEST)
    return doc


class ConnectReplay:
    """Bounded owner-object and Core-epoch scoped idempotency table.

    This is an in-memory operation ledger, not caller authentication. The Core
    adapter must establish the exact current host instance independently.
    """

    def __init__(self, capacity: int = 256) -> None:
        self.capacity = _integer(capacity, minimum=1, maximum=65536)
        self._records: OrderedDict[
            tuple[int, int, str], tuple[object, str, str, bool]
        ] = OrderedDict()

    def connect(
        self, owner: object, epoch: int, message: Mapping[str, Any], operation_id: str
    ) -> str:
        _integer(epoch)
        _string(operation_id, limit=128)
        if type(message) is not dict or message.get("op") != "connect":
            raise ProtocolError("connect message required")
        try:
            normalized_bytes = json.dumps(
                message, ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise ProtocolError("invalid connect input") from exc
        message = parse_control(normalized_bytes)
        request_id = message["request_id"]
        normalized = json.dumps(
            message, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        key = (id(owner), epoch, request_id)
        record = self._records.get(key)
        if record is not None and record[0] is owner:
            if record[1] != normalized:
                raise ProtocolError("request_conflict")
            return record[2]
        if len(self._records) >= self.capacity:
            for old_key, old_record in list(self._records.items()):
                if old_record[3]:
                    del self._records[old_key]
                    break
            else:
                raise ProtocolError("busy")
        self._records[key] = (owner, normalized, operation_id, False)
        return operation_id

    def finish(self, owner: object, epoch: int, request_id: str) -> None:
        key = (id(owner), _integer(epoch), _uuid(request_id))
        record = self._records.get(key)
        if record is not None and record[0] is owner:
            self._records[key] = (*record[:3], True)

    def revoke_owner(self, owner: object, epoch: int) -> None:
        """Forget settled operations only; pending work retains its cleanup owner."""
        for key, record in list(self._records.items()):
            if key[1] == epoch and record[0] is owner and record[3]:
                del self._records[key]
