"""Complete independent ZIPs must satisfy the production admission contracts."""

import base64
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ygl_test_subject.bootstrap.loader import validate_core_release
from ygl_test_subject.extensions.hot_update_protocol import parse_release
from ygl_test_subject.extensions.runtime_bundle import (
    RuntimeBundleError,
    RuntimeBundleStore,
)

from scripts import build_dashboard_zip as dashboard
from scripts import release_envelope as envelope

ROOT = Path(__file__).resolve().parents[2]


class CompleteReleaseTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {
            "main.py": b'@register("sample", "tests", "fixture", "1.0.0")\nclass Shell: pass\n',
            "metadata.yaml": b"name: sample\nauthor: tests\nversion: 1.0.0\n",
            "ygl-plugin.json": json.dumps(
                {
                    "schema": "ygl.plugin/1",
                    "role": "core",
                    "package_id": "ygl_core",
                    "manifest": "release.json",
                    "control_protocol": {"name": "ygl.control", "major": 1},
                }
            ).encode(),
            "infrastructure/sqlite/migrations/0000.sql": b"CREATE TABLE sample(id);",
        }

    def unpack(self, payload):
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
            archive.extractall(self.root)
        return files

    def make(self, role="core", signed=True):
        files = dict(self.files)
        if role == "dlc":
            descriptor = json.loads(files["ygl-plugin.json"])
            descriptor.update(
                role="dlc",
                package_id="sample",
                manifest="packages/sample/yomihime.manifest.json",
            )
            files["ygl-plugin.json"] = json.dumps(descriptor).encode()
            files["packages/sample/yomihime.manifest.json"] = b"{}"
        return envelope.complete(
            files, ROOT, role, signing_key=self.key if signed else None
        )

    def test_core_raw_signature_exact_inventory_and_budget(self):
        payload = self.make()
        self.assertLess(len(payload), 16_000_000)
        files = self.unpack(payload)
        release = parse_release(files["release.json"])
        self.assertEqual(
            {row["path"] for row in release["files"]},
            set(files) - {"release.json", "release.sig.json"},
        )
        self.assertIn("release-key.json", {row["path"] for row in release["files"]})
        self.assertEqual(release["channel"], "preview")
        self.assertEqual(release["release_sequence"], 1)
        bundle = RuntimeBundleStore().capture(self.root, release, kind="core")
        self.addCleanup(bundle.release)
        self.assertEqual(
            validate_core_release(
                files["release.json"], files["release.sig.json"], bundle
            )["role"],
            "core",
        )
        for raw in (files["release.json"] + b" ", files["release.json"][:-1]):
            with self.assertRaises(RuntimeBundleError):
                validate_core_release(raw, files["release.sig.json"], bundle)
        with self.assertRaises(RuntimeBundleError):
            validate_core_release(files["release.json"], None, bundle)

    def test_core_cannot_be_unsigned(self):
        with self.assertRaisesRegex(ValueError, "requires an Ed25519"):
            self.make(signed=False)

    def test_signed_and_unsigned_dlc_are_complete(self):
        for signed in (False, True):
            files = self.unpack(self.make("dlc", signed))
            release = parse_release(files["release.json"])
            self.assertEqual(release["package_id"], "sample")
            self.assertEqual("release.sig.json" in files, signed)
            if signed:
                signature = json.loads(files["release.sig.json"])
                self.key.public_key().verify(
                    base64.b64decode(signature["signature_base64"]),
                    files["release.json"],
                )
            # Clear prior signature before testing the distinct unsigned artifact.
            bundle = RuntimeBundleStore().capture(self.root, release, kind="dlc")
            bundle.release()

    def test_tamper_missing_and_extra_files_fail_closed(self):
        for mutation in ("tamper", "missing", "extra"):
            with self.subTest(mutation=mutation):
                files = self.unpack(self.make())
                main = self.root / "main.py"
                if mutation == "tamper":
                    main.write_bytes(b"changed")
                elif mutation == "missing":
                    main.unlink()
                else:
                    (self.root / "extra.txt").write_bytes(b"unexpected")
                with self.assertRaises(RuntimeBundleError):
                    RuntimeBundleStore().capture(
                        self.root, parse_release(files["release.json"]), kind="core"
                    )
                (self.root / "extra.txt").unlink(missing_ok=True)

    def test_path_collisions_and_size_budgets(self):
        for files in (
            {"../x": b"x"},
            {"/x": b"x"},
            {"a": b"x", "a/b": b"x"},
            {"A/x": b"x", "a/y": b"x"},
            {"release.json": b"x"},
            {"big": b"x" * (1024 * 1024 + 1)},
        ):
            with self.subTest(paths=list(files)), self.assertRaises(ValueError):
                envelope.validate_files(files, "dlc")
        with (
            patch.object(envelope, "MAX_ZIP_BYTES", 1),
            self.assertRaisesRegex(ValueError, "smaller than"),
        ):
            self.make()

    def test_repeat_build_same_key_has_identical_bytes_and_source_receipt(self):
        one = self.make()
        self.assertEqual(one, self.make())
        files = self.unpack(one)
        receipt = json.loads(files["BUILD.json"])
        self.assertEqual(
            receipt["source_files"]["main.py"],
            hashlib.sha256(self.files["main.py"]).hexdigest(),
        )
        self.assertIn("build_release.py", receipt["builder_files"])
        self.assertTrue(receipt["dirty"])

    def test_real_core_payload_has_private_sdk_bootstrap_sql_and_no_external_dlc(self):
        files, _ = dashboard.capture_payload(ROOT)
        for name in (
            "bootstrap/loader.py",
            "yomihime_game_link_sdk/declarations.py",
            "yomihime_game_link_sdk/py.typed",
            "infrastructure/sqlite/migrations/0000_schema_migrations.sql",
            "_conf_schema.json",
        ):
            self.assertIn(name, files)
        self.assertFalse(any("steam_dlc" in name for name in files))
        self.assertFalse(
            any(Path(name).suffix in (".ttf", ".otf", ".pem", ".key") for name in files)
        )
        self.assertFalse(
            any(
                set(Path(name).parts)
                & {".agent-workspace", ".git", "tests", "node_modules", "__pycache__"}
                for name in files
            )
        )

    def test_host_numeric_version_comparator_and_metadata_identity(self):
        host = (
            ROOT.parent / "astrbot-auth-4291/astrbot/core/utils/version_comparator.py"
        )
        if not host.exists():
            host = (
                Path(__import__("os").environ["YGL_TEST_ASTRBOT_SOURCE"])
                / "core/utils/version_comparator.py"
            )
        spec = importlib.util.spec_from_file_location("fixed_host_version", host)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        compare = module.VersionComparator.compare_version
        for new, old in (
            ("0.2.0", "0.1.13"),
            ("v0.3.0", "v0.2.0-beta.4"),
            ("0.10.0", "0.9.0"),
        ):
            self.assertEqual(compare(new, old), 1)
            self.assertEqual(compare(old, new), -1)
        info = envelope.metadata(
            {"metadata.yaml": (ROOT / "metadata.yaml").read_bytes()}
        )
        self.assertEqual(
            info,
            {
                "name": "astrbot_plugin_yomihime_game_link",
                "author": "yomihime",
                "version": "0.2.0",
            },
        )

    def test_metadata_identity_mismatch_rejected(self):
        for field in ("name", "author", "version"):
            files = dict(self.files)
            files["metadata.yaml"] = files["metadata.yaml"].replace(
                {"name": b"sample", "author": b"tests", "version": b"1.0.0"}[field],
                {"version": b"1.0.1"}.get(field, b"different"),
            )
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "register author/name/version"),
            ):
                envelope.complete(files, ROOT, "core", signing_key=self.key)

    def test_core_source_mutation_preserves_previous_archive(self):
        output = self.root / "candidate.zip"
        output.write_bytes(b"previous artifact")
        first = dict(self.files)
        changed = {**first, "main.py": first["main.py"] + b"# changed"}
        with (
            patch.object(
                dashboard, "capture_payload", side_effect=[(first, []), (changed, [])]
            ),
            self.assertRaisesRegex(ValueError, "source changed"),
        ):
            dashboard.build(
                archive_path=output, repository_root=ROOT, signing_key=self.key
            )
        self.assertEqual(output.read_bytes(), b"previous artifact")

    def test_complete_real_core_mount_keeps_private_sdk_types(self):
        import importlib

        files, _ = dashboard.capture_payload(ROOT)
        packed = self.unpack(
            envelope.complete(files, ROOT, "core", signing_key=self.key)
        )
        bundle = RuntimeBundleStore().capture(
            self.root, parse_release(packed["release.json"]), kind="core"
        )
        self.addCleanup(bundle.release)
        validate_core_release(
            packed["release.json"], packed["release.sig.json"], bundle
        )
        bundle.mount_core()
        private = importlib.import_module(bundle.namespace + ".yomihime_game_link_sdk")
        registry = importlib.import_module(bundle.namespace + ".core.registry")
        self.assertIn(bundle.namespace, private.CapabilityResult.__module__)
        self.assertEqual(registry.__name__, bundle.namespace + ".core.registry")
