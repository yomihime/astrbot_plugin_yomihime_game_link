"""Create disposable native smoke derivatives of exact package candidates."""

import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

HOST_RELEASE_SCHEMA = "ygl.release/2"
LEGACY_RELEASE_SCHEMA = "ygl.release/1"


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def read_release(source):
    with zipfile.ZipFile(source) as archive:
        raw = archive.read("release.json")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _inventory(files):
    rows = [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            (
                row["path"] + "\0" + str(row["size"]) + "\0" + row["sha256"] + "\n"
            ).encode()
        )
    return rows, digest.hexdigest()


def derive(
    source, target, key=None, *, version=None, sequence=2, unknown=None, repo_root=None
):
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate fixture source entry")
        files = {name: archive.read(name) for name in names}
    release = json.loads(files.pop("release.json"))
    signature = files.pop("release.sig.json", None)
    fields = {"schema", "plugin_id", "package_id", "release", "payload_digest", "files"}
    ordinary = release["schema"] == HOST_RELEASE_SCHEMA
    if ordinary:
        if (
            key is not None
            or signature is not None
            or any(
                name.casefold() in {"release.sig.json", "release-key.json"}
                for name in files
            )
        ):
            raise ValueError("ordinary fixture source must be unsigned")
    elif release["schema"] == LEGACY_RELEASE_SCHEMA and key is not None:
        fields.update(("channel", "release_sequence"))
    else:
        raise ValueError("legacy fixture requires an explicit signing key")
    rows, digest = _inventory(files)
    if (
        set(release) != fields
        or release["files"] != rows
        or release["payload_digest"] != digest
    ):
        raise ValueError("fixture source release inventory mismatch")
    if unknown:
        original = files["main.py"].decode()
        name = "astrbot_plugin_hot_update_host_" + unknown
        shell = original.replace("astrbot_plugin_yomihime_game_link_steam_dlc", name)
        shell = shell.replace(
            "packages/steam_dlc/yomihime.manifest.json",
            f"packages/{unknown}/yomihime.manifest.json",
        )
        files = {
            "main.py": shell.encode(),
            "__init__.py": b"",
            "requirements.txt": b"",
            "metadata.yaml": f"name: {name}\nauthor: yomihime\nversion: 1.0.0\ndesc: Disposable unknown native DLC fixture\nrepo: https://github.com/offline-fixture/{name}\n".encode(),
        }
        manifest = json.loads(
            (repo_root / "examples/modules/minimal/yomihime.manifest.json").read_bytes()
        )
        manifest.update(
            package_id=unknown,
            package_version="1.0.0",
            release="1.0.0",
            module_contract={"major": 2, "minimum_minor": 0},
            required_features=[],
            optional_features=[],
            data={"schema": 1, "writes": 1, "readable": [1]},
        )
        manifest["modules"][0].update(
            route=unknown, pages=[], resources=[], config_fields=[]
        )
        source_module = (
            (repo_root / "examples/modules/minimal/module.py")
            .read_bytes()
            .replace(b"from yomihime_game_link_sdk", b"from ._api_v2")
        )
        files[f"packages/{unknown}/module.py"] = source_module
        files[f"packages/{unknown}/yomihime.manifest.json"] = encode(manifest)
        files["ygl-plugin.json"] = encode(
            {
                "schema": "ygl.plugin/1",
                "role": "dlc",
                "package_id": unknown,
                "manifest": f"packages/{unknown}/yomihime.manifest.json",
                "control_protocol": {"name": "ygl.control", "major": 1},
            }
        )
        release.update(
            package_id=unknown, plugin_id={"author": "yomihime", "name": name}
        )
        version = "1.0.0"
    if version:
        old = release["release"].encode()
        updated = version.encode()
        for path in tuple(files):
            if path == "main.py":
                files[path] = files[path].replace(
                    b'"' + old + b'"', b'"' + updated + b'"'
                )
            elif path == "metadata.yaml":
                files[path] = re.sub(
                    rb"(?m)^(version: v?)" + re.escape(old) + rb"(?=\r?$)",
                    lambda match: match[1] + updated,
                    files[path],
                )
            elif path.endswith("/yomihime.manifest.json"):
                manifest = json.loads(files[path])
                manifest["package_version"] = version
                if "release" in manifest:
                    manifest["release"] = version
                for module in manifest["modules"]:
                    if module["module_version"] == release["release"]:
                        module["module_version"] = version
                files[path] = encode(manifest)
        release["release"] = version
    if not ordinary:
        release["release_sequence"] = sequence
    files["HOT_UPDATE_HOST_FIXTURE.json"] = encode(
        {
            "synthetic": True,
            "parent_zip_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "purpose": "offline native lifecycle verification only",
        }
    )
    release["files"], release["payload_digest"] = _inventory(files)
    raw = encode(release)
    files["release.json"] = raw
    if not ordinary:
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        files["release.sig.json"] = encode(
            {
                "algorithm": "ed25519",
                "key_id": hashlib.sha256(public).hexdigest(),
                "signature_base64": base64.b64encode(key.sign(raw)).decode(),
            }
        )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    target.write_bytes(stream.getvalue())
    return target
