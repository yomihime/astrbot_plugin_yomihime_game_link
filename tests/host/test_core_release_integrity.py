"""Core native-admin bootstrap integrity and conservative cache compatibility."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from ygl_test_subject.bootstrap.loader import (
    core_migration_inventory,
    validate_core_release,
)
from ygl_test_subject.extensions.runtime_bundle import (
    RuntimeBundleError,
    RuntimeBundleStore,
)

from tests.extensions.test_private_runtime_bundle import _release, _write


class CoreReleaseIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.key_id = hashlib.sha256(public).hexdigest()
        self.files = {
            "main.py": b"# closed entry",
            "ygl-plugin.json": json.dumps(
                {
                    "schema": "ygl.plugin/1",
                    "role": "core",
                    "package_id": "ygl_core",
                    "manifest": "release.json",
                    "control_protocol": {"name": "ygl.control", "major": 1},
                }
            ).encode(),
            "release-key.json": json.dumps(
                {
                    "algorithm": "ed25519",
                    "key_id": self.key_id,
                    "public_key_base64": base64.b64encode(public).decode(),
                }
            ).encode(),
            "infrastructure/sqlite/migrations/0000.sql": b"CREATE TABLE sample(id);",
        }
        _write(self.root, self.files)
        self.release = _release(self.files, package_id="ygl_core")
        self.raw = json.dumps(self.release).encode()
        self.bundle = RuntimeBundleStore().capture(self.root, self.release, kind="core")
        self.addCleanup(self.bundle.release)
        self.signature = {
            "algorithm": "ed25519",
            "key_id": self.key_id,
            "signature_base64": base64.b64encode(self.key.sign(self.raw)).decode(),
        }

    def test_signed_core_and_strict_envelope(self):
        self.assertEqual(
            validate_core_release(
                self.raw, json.dumps(self.signature).encode(), self.bundle
            )["role"],
            "core",
        )
        for signature in (
            None,
            b"{}",
            json.dumps({**self.signature, "extra": True}).encode(),
            json.dumps({**self.signature, "signature_base64": "!"}).encode(),
            json.dumps({**self.signature, "key_id": "0" * 64}).encode(),
            json.dumps(
                {
                    **self.signature,
                    "signature_base64": base64.b64encode(b"0" * 64).decode(),
                }
            ).encode(),
        ):
            with (
                self.subTest(signature=signature),
                self.assertRaises(RuntimeBundleError),
            ):
                validate_core_release(self.raw, signature, self.bundle)
        with self.assertRaises(RuntimeBundleError):
            validate_core_release(
                self.raw + b" ", json.dumps(self.signature).encode(), self.bundle
            )

    def test_migration_inventory_ignores_code_but_requires_exact_schema(self):
        original = core_migration_inventory(self.bundle)
        self.assertEqual(len(original), 1)
        self.files["main.py"] = b"# compatible new code"
        _write(self.root, self.files)
        newer = RuntimeBundleStore().capture(
            self.root, _release(self.files, package_id="ygl_core"), kind="core"
        )
        self.addCleanup(newer.release)
        self.assertEqual(core_migration_inventory(newer), original)
        self.files["infrastructure/sqlite/migrations/0000.sql"] += b"-- changed"
        _write(self.root, self.files)
        changed = RuntimeBundleStore().capture(
            self.root, _release(self.files, package_id="ygl_core"), kind="core"
        )
        self.addCleanup(changed.release)
        self.assertNotEqual(core_migration_inventory(changed), original)
