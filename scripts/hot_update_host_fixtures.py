"""Disposable signed derivatives of exact candidates, never release builders."""

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def derive(
    source, target, key, *, version=None, sequence=2, unknown=None, repo_root=None
):
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    release = json.loads(files.pop("release.json"))
    files.pop("release.sig.json", None)
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
        files["main.py"] = files["main.py"].replace(b'"0.3.0"', b'"1.0.0"')
    if version:
        old = release["release"]
        for path in tuple(files):
            if (
                path == "main.py"
                or path == "metadata.yaml"
                or path.endswith("yomihime.manifest.json")
            ):
                files[path] = files[path].replace(old.encode(), version.encode())
        release["release"] = version
    release["release_sequence"] = sequence
    files["HOT_UPDATE_HOST_FIXTURE.json"] = encode(
        {
            "synthetic": True,
            "parent_zip_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "purpose": "offline native lifecycle verification only",
        }
    )
    release["files"] = [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    digest = hashlib.sha256()
    for row in release["files"]:
        digest.update(
            (
                row["path"] + "\0" + str(row["size"]) + "\0" + row["sha256"] + "\n"
            ).encode()
        )
    release["payload_digest"] = digest.hexdigest()
    raw = encode(release)
    files["release.json"] = raw
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
