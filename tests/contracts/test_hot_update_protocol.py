"""Host-independent v1 envelope and replay contract tests."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from ygl_test_subject.extensions.disk_manifest import ManifestError, parse_manifest
from ygl_test_subject.extensions.hot_update_protocol import (
    ConnectReplay,
    ProtocolError,
    b05_manifest_bytes,
    check_data_compatibility,
    negotiate_manifest,
    parse_control,
    parse_package_manifest,
    parse_plugin_descriptor,
    parse_release,
    payload_digest,
)

from tests.extensions.test_disk_manifest import valid_document


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def manifest() -> dict:
    doc = valid_document()
    doc.update(
        package_id="new_unseen_dlc",
        release="1.2.3",
        package_version="1.2.3",
        module_contract={"major": 2, "minimum_minor": 0},
        required_features=["storage.records/1"],
        optional_features=["send.outbox/1"],
        data={"schema": 1, "readable": [1], "writes": 1},
    )
    doc["modules"][0]["capabilities"][0]["input_schema"]["properties"] = {
        "value": {"type": "number", "minimum": 0.5}
    }
    return doc


def control(op: str, **kwargs: object) -> dict:
    return {"protocol": "ygl.control/1", "op": op, **kwargs}


class ProtocolTests(unittest.TestCase):
    def test_new_unseen_package_and_b05_validation(self):
        doc = parse_package_manifest(encoded(manifest()))
        self.assertEqual(
            parse_manifest(b05_manifest_bytes(doc)).package_id, "new_unseen_dlc"
        )
        self.assertEqual(
            negotiate_manifest(doc, 0, ["storage.records/1"]), ("storage.records/1",)
        )
        self.assertEqual(
            negotiate_manifest(doc, 1, ["storage.records/1", "send.outbox/1"]),
            ("send.outbox/1", "storage.records/1"),
        )
        changed = copy.deepcopy(doc)
        changed["modules"][0]["arbitrary_python_credentials"] = True
        parse_package_manifest(
            encoded(changed)
        )  # Envelope cannot interpret B05 fields.
        with self.assertRaises(ManifestError):
            parse_manifest(b05_manifest_bytes(changed))

    def test_manifest_strict_fields_and_feature_semantics(self):
        base = manifest()
        mutations = [
            ("permissions", {"python": True}),
            ("package_id", "publisher/package"),
            ("package_id", ""),
            ("release", "v1.2.3"),
            ("schema_version", True),
            ("module_contract", {"major": True, "minimum_minor": 0}),
            ("required_features", ["storage.records/1", "storage.records/1"]),
            ("optional_features", ["storage.records/1"]),
            ("data", {"schema": 1, "readable": [1, 1], "writes": 1}),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                doc = copy.deepcopy(base)
                doc[key] = value
                with self.assertRaises(ProtocolError):
                    parse_package_manifest(encoded(doc))
        doc = parse_package_manifest(encoded(base))
        with_policy = copy.deepcopy(base)
        with_policy["source_policy"] = "policy/sources.json"
        with self.assertRaises(ProtocolError):
            parse_package_manifest(encoded(with_policy))
        with_policy["required_features"].append("source.policies/1")
        parsed_policy = parse_package_manifest(encoded(with_policy))
        self.assertNotIn("source_policy", json.loads(b05_manifest_bytes(parsed_policy)))
        with_policy["source_policy"] = "../policy.json"
        with self.assertRaises(ProtocolError):
            parse_package_manifest(encoded(with_policy))
        with self.assertRaisesRegex(ProtocolError, "missing_feature"):
            negotiate_manifest(doc, 0, [])
        doc["module_contract"]["minimum_minor"] = 3
        with self.assertRaisesRegex(ProtocolError, "unsupported_contract"):
            negotiate_manifest(doc, 2, ["storage.records/1"])

    def test_data_changes_refused_without_migration(self):
        doc = parse_package_manifest(encoded(manifest()))
        check_data_compatibility(doc, 1, 1, 1)
        for args in [(2, 1, 1), (1, 2, 1), (1, 1, 2)]:
            with (
                self.subTest(args=args),
                self.assertRaisesRegex(ProtocolError, "data_incompatible"),
            ):
                check_data_compatibility(doc, *args)

    def test_ordinary_prerelease_and_build_metadata_are_profile_specific(self):
        files = [{"path": "main.py", "size": 0, "sha256": "a" * 64}]
        release = {
            "schema": "ygl.release/2",
            "plugin_id": {"author": "A", "name": "N"},
            "package_id": "new_unseen_dlc",
            "files": files,
            "payload_digest": payload_digest(files),
        }
        schemas = Path(__file__).parents[2] / "schemas"
        release_schema = Draft202012Validator(
            json.loads((schemas / "ygl-release-v2.json").read_text())
        )
        package_schema = Draft202012Validator(
            json.loads((schemas / "ygl-package-v1.json").read_text())
        )
        for version in ("1.2.3-beta.4+build.07", "10.20.30-rc.1", "1.2.3+linux"):
            with self.subTest(version=version):
                candidate = release | {"release": version}
                business = manifest() | {"release": version, "package_version": version}
                release_schema.validate(candidate)
                package_schema.validate(business)
                self.assertEqual(parse_release(encoded(candidate))["release"], version)
                self.assertEqual(
                    parse_manifest(
                        b05_manifest_bytes(
                            parse_package_manifest(encoded(business), host_install=True)
                        )
                    ).package_version,
                    version,
                )
                with self.assertRaises(ProtocolError):
                    parse_release(
                        encoded(
                            candidate
                            | {
                                "schema": "ygl.release/1",
                                "channel": "preview",
                                "release_sequence": 1,
                            }
                        )
                    )
                with self.assertRaises(ProtocolError):
                    parse_package_manifest(encoded(business))
        for version in (
            "v1.2.3",
            "01.2.3",
            "1.2.3-beta.04",
            "1.2.3-beta..4",
            "1.2.3+",
            "1.2.3-" + "a" * 123,
        ):
            with self.subTest(version=version):
                candidate = release | {"release": version}
                business = manifest() | {"release": version, "package_version": version}
                self.assertFalse(release_schema.is_valid(candidate))
                self.assertFalse(package_schema.is_valid(business))
                with self.assertRaises(ProtocolError):
                    parse_release(encoded(candidate))
                with self.assertRaises(ProtocolError):
                    parse_package_manifest(encoded(business), host_install=True)

    def test_descriptor_and_release_digest(self):
        descriptor = {
            "schema": "ygl.plugin/1",
            "role": "dlc",
            "package_id": "new_unseen_dlc",
            "manifest": "meta/manifest.json",
            "control_protocol": {"name": "ygl.control", "major": 1},
        }
        self.assertEqual(
            parse_plugin_descriptor(encoded(descriptor))["manifest"],
            "meta/manifest.json",
        )
        for major in (True, 1.0):
            with self.subTest(major=major), self.assertRaises(ProtocolError):
                parse_plugin_descriptor(
                    encoded(
                        {
                            **descriptor,
                            "control_protocol": {"name": "ygl.control", "major": major},
                        }
                    )
                )
        for path in [
            "../x",
            "/x",
            "a\\b",
            "a//b",
            "a/./b",
            "C:/x",
            "a\u0000b",
            "a\u0080b",
            "é" * 600,
        ]:
            doc = {**descriptor, "manifest": path}
            with self.subTest(path=path), self.assertRaises(ProtocolError):
                parse_plugin_descriptor(encoded(doc))
        files = [
            {"path": "main.py", "size": 3, "sha256": "a" * 64},
            {"path": "metadata.yaml", "size": 0, "sha256": "b" * 64},
        ]
        raw = (
            b"main.py\x003\x00"
            + b"a" * 64
            + b"\nmetadata.yaml\x000\x00"
            + b"b" * 64
            + b"\n"
        )
        self.assertEqual(payload_digest(files), hashlib.sha256(raw).hexdigest())
        release = {
            "schema": "ygl.release/1",
            "plugin_id": {"author": "Author/Name", "name": "Plugin"},
            "package_id": "new_unseen_dlc",
            "release": "1.2.3",
            "channel": "stable",
            "release_sequence": 10,
            "payload_digest": payload_digest(files),
            "files": files,
        }
        self.assertEqual(
            parse_release(encoded(release))["plugin_id"]["author"], "Author/Name"
        )
        for sequence in (9, 10):
            self.assertEqual(
                parse_release(encoded({**release, "release_sequence": sequence}))[
                    "release_sequence"
                ],
                sequence,
            )
        for changed in [
            {**release, "channel": "edge"},
            *(
                {**release, "release_sequence": value}
                for value in (True, 1.0, -1, 2**53)
            ),
        ]:
            with self.assertRaises(ProtocolError):
                parse_release(encoded(changed))
        for bad_files in [
            files + [files[0]],
            files + [{**files[0], "path": "MAIN.py"}],
            files + [{**files[0], "path": "release.json"}],
            files + [{**files[0], "path": "RELEASE.JSON"}],
            files + [{**files[0], "path": "a/../b"}],
            [{**files[0], "path": "A/x"}, {**files[1], "path": "a/y"}],
            [{**files[0], "path": "A"}, {**files[1], "path": "A/x"}],
        ]:
            with self.assertRaises(ProtocolError):
                payload_digest(bad_files)
        release["files"] = files[::-1]
        parse_release(
            encoded(release)
        )  # File order is immaterial; digest is deterministic.
        release["files"][0]["size"] = True
        with self.assertRaises(ProtocolError):
            parse_release(encoded(release))

    def test_core_release_descriptor_is_not_a_business_manifest(self):
        core = {
            "schema": "ygl.plugin/1",
            "role": "core",
            "package_id": "ygl_core",
            "manifest": "release.json",
            "control_protocol": {"name": "ygl.control", "major": 1},
        }
        self.assertEqual(parse_plugin_descriptor(encoded(core)), core)
        schema = json.loads(
            (Path(__file__).parents[2] / "schemas/ygl-plugin-v1.json").read_text()
        )
        validator = Draft202012Validator(schema)
        validator.validate(core)
        for invalid in (
            {**core, "manifest": "business.json"},
            {**core, "package_id": "ff14"},
            {**core, "role": "dlc"},
            {**core, "role": "dlc", "manifest": "release.sig.json"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ProtocolError):
                parse_plugin_descriptor(encoded(invalid))
            self.assertTrue(list(validator.iter_errors(invalid)))

    def test_json_limits_duplicate_and_unsafe_values(self):
        good = control("describe")
        for raw in [
            b'{"protocol":"ygl.control/1","op":"describe","op":"connect"}',
            encoded({**good, "unexpected": True}),
            b'{"protocol":"ygl.control/1","op":"describe","x":NaN}',
            b'{"protocol":"ygl.control/1","op":"describe","x":9007199254740992}',
            b"\xff",
            b" " * 65537,
            b'{"protocol":"ygl.control/1","op":"describe","x":"\\ud800"}',
        ]:
            with self.subTest(raw=raw[:60]), self.assertRaises(ProtocolError):
                parse_control(raw)
        nested = good | {
            "x": [[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]
        }  # Unknown field also rejected.
        with self.assertRaises(ProtocolError):
            parse_control(encoded(nested))

    def test_json_schemas_accept_fixtures(self):
        schemas = Path(__file__).parents[2] / "schemas"
        fixtures = {
            "ygl-plugin-v1.json": {
                "schema": "ygl.plugin/1",
                "role": "dlc",
                "package_id": "new_unseen_dlc",
                "manifest": "manifest.json",
                "control_protocol": {"name": "ygl.control", "major": 1},
            },
            "ygl-package-v1.json": manifest(),
            "ygl-control-v1.json": control(
                "connect",
                request_id="aaaaaaaa-1111-4111-8111-111111111111",
                descriptor_digest="a" * 64,
            ),
            "ygl-release-v1.json": {
                "schema": "ygl.release/1",
                "plugin_id": {"author": "A", "name": "N"},
                "package_id": "new_unseen_dlc",
                "release": "1.2.3",
                "channel": "preview",
                "release_sequence": 0,
                "payload_digest": "a" * 64,
                "files": [{"path": "main.py", "size": 0, "sha256": "a" * 64}],
            },
            "ygl-release-v2.json": {
                "schema": "ygl.release/2",
                "plugin_id": {"author": "A", "name": "N"},
                "package_id": "new_unseen_dlc",
                "release": "1.2.3",
                "payload_digest": "a" * 64,
                "files": [{"path": "main.py", "size": 0, "sha256": "a" * 64}],
            },
        }
        for name, fixture in fixtures.items():
            with self.subTest(name=name):
                schema = json.loads((schemas / name).read_text())
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(fixture)

    def test_control_shapes_and_replay(self):
        request_id = "aaaaaaaa-1111-4111-8111-111111111111"
        message = control("connect", request_id=request_id, descriptor_digest="a" * 64)
        self.assertEqual(parse_control(encoded(message)), message)
        parse_control(encoded(control("status", operation_id="op")))
        parse_control(encoded(control("status", lease="b" * 64)))
        parse_control(encoded(control("disconnect", lease="b" * 64)))
        parse_control(encoded(control("retire")))
        for invalid in [
            control("status"),
            control("disconnect", lease="old"),
            control("retire", lease="b" * 64),
            control("retire", package_id="another_package"),
            {**message, "request_id": request_id.upper()},
            {**message, "descriptor_digest": "A" * 64},
            {**message, "permissions": []},
        ]:
            with self.subTest(invalid=invalid), self.assertRaises(ProtocolError):
                parse_control(encoded(invalid))
        owner, other = object(), object()
        replay = ConnectReplay(capacity=2)
        self.assertEqual(replay.connect(owner, 7, message, "op1"), "op1")
        self.assertEqual(
            replay.connect(owner, 7, dict(reversed(list(message.items()))), "op2"),
            "op1",
        )
        message["descriptor_digest"] = "b" * 64
        with self.assertRaisesRegex(ProtocolError, "request_conflict"):
            replay.connect(owner, 7, message, "op2")
        self.assertEqual(replay.connect(other, 7, message, "op2"), "op2")
        with self.assertRaisesRegex(ProtocolError, "busy"):
            replay.connect(owner, 8, message, "op3")
        replay.finish(owner, 7, request_id)
        self.assertEqual(replay.connect(owner, 8, message, "op3"), "op3")
        replay.revoke_owner(other, 7)
        with self.assertRaisesRegex(ProtocolError, "busy"):
            replay.connect(owner, 9, message, "op4")
        cyclic = dict(message)
        cyclic["cycle"] = cyclic
        with self.assertRaises(ProtocolError):
            replay.connect(owner, 7, cyclic, "op5")


if __name__ == "__main__":
    unittest.main()
