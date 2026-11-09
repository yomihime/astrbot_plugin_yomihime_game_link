"""Private Core release capture plus optional development-wheel boundaries."""

from __future__ import annotations

import ast
import hashlib
import importlib
import os
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ygl_test_subject.bootstrap.loader import validate_core_release
from ygl_test_subject.extensions.hot_update_protocol import parse_release
from ygl_test_subject.extensions.runtime_bundle import RuntimeBundleStore

import scripts.build_dashboard_zip as zip_builder
from scripts.build_dashboard_zip import (
    EXPECTED_WHEEL_SHA256,
    FONT_ASSETS,
    HOST_METADATA_FILES,
    MAINTENANCE_HELPER_FILES,
    OPERATOR_SCRIPT_FILES,
    PAGE_FILES,
    ROOT_FILES,
    RUNTIME_DIRS,
    _reject_link,
    build,
    included_files,
    validate_source_path,
    validate_wheel,
)
from scripts.build_release import build_sdk_wheel

ROOT = Path(__file__).resolve().parents[2]


def _write_operator_script_fixtures(root: Path) -> None:
    for filename in FONT_ASSETS:
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / filename).read_bytes())
    for filename in (
        *OPERATOR_SCRIPT_FILES,
        *HOST_METADATA_FILES,
        *PAGE_FILES,
        *MAINTENANCE_HELPER_FILES,
    ):
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"operator script fixture")


class SDKBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="ygl-h-sdk-")
        cls.temp_root = Path(cls.temp.name).resolve()
        wheel_input = os.environ.get("YGL_TEST_SDK_WHEEL")
        cls.wheel = (
            Path(wheel_input).resolve(strict=True)
            if wheel_input
            else build_sdk_wheel(cls.temp_root / "build", ROOT)
        )
        validate_wheel(cls.wheel)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_thin_entry_has_no_eager_business_or_canonical_sdk_import(self):
        tree = ast.parse((ROOT / "main.py").read_text())
        imports = [
            node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        names = [getattr(node, "module", "") or "" for node in imports]
        self.assertIn("bootstrap.loader", names)
        self.assertFalse(any("yomihime_game_link_sdk" in name for name in names))
        self.assertFalse(any("adapters.astrbot.runtime" in name for name in names))

    def test_zip_builder_rejects_runtime_escape_and_file_links(self) -> None:
        repository = self.temp_root / "synthetic-plugin-root"
        repository.mkdir()
        for name in ROOT_FILES:
            (repository / name).write_text("fixture", encoding="utf-8")
        for name in RUNTIME_DIRS:
            (repository / name).mkdir()
        _write_operator_script_fixtures(repository)

        outside = self.temp_root / "outside.py"
        outside.write_text("private sentinel", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes its allowed root"):
            validate_source_path(outside, repository / "core", "fixture source")

        runtime_file = repository / "core" / "private.py"
        try:
            os.link(outside, runtime_file)
        except OSError:
            try:
                os.symlink(outside, runtime_file)
            except OSError:
                pass
        if runtime_file.exists() or runtime_file.is_symlink():
            with self.assertRaisesRegex(ValueError, "hard link|symlink"):
                included_files(repository)
        else:
            # Link creation can be disabled on Windows; the executable path
            # containment check above still exercises the escape rejection.
            self.assertRaisesRegex(
                ValueError,
                "escapes its allowed root",
                validate_source_path,
                outside,
                repository / "core",
                "fixture source",
            )

    def test_link_and_reparse_metadata_are_rejected_without_os_link_support(
        self,
    ) -> None:
        probe = self.temp_root / "metadata-probe"
        probe.write_text("ordinary file", encoding="utf-8")
        cases = (
            (stat.S_IFLNK, 1, 0, "symlink"),
            (stat.S_IFREG, 2, 0, "hard link"),
            (stat.S_IFREG, 1, 0x400, "reparse point"),
        )
        for mode, links, attributes, message in cases:
            metadata = SimpleNamespace(
                st_mode=mode,
                st_nlink=links,
                st_file_attributes=attributes,
            )
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                _reject_link(probe, metadata, "fixture input")

    def test_wheel_parser_uses_the_same_bytes_that_passed_the_digest(self) -> None:
        original_path = self.temp_root / "pinned-snapshot.whl"
        original_path.write_bytes(self.wheel.read_bytes())
        replacement = self.temp_root / "replacement.whl"
        with (
            zipfile.ZipFile(self.wheel) as source,
            zipfile.ZipFile(
                replacement, "w", compression=zipfile.ZIP_DEFLATED
            ) as output,
        ):
            authenticated = source.read("yomihime_game_link_sdk/py.typed")
            for name in source.namelist():
                data = source.read(name)
                if name == "yomihime_game_link_sdk/py.typed":
                    data = b"replaced after digest snapshot\n"
                output.writestr(name, data)

        read_snapshot = zip_builder._read_snapshot

        def read_then_replace(path, allowed_root=None, label="Package input"):
            snapshot = read_snapshot(path, allowed_root, label)
            replacement.replace(original_path)
            return snapshot

        with mock.patch.object(
            zip_builder, "_read_snapshot", side_effect=read_then_replace
        ):
            package = zip_builder.validate_wheel(original_path)
        self.assertEqual(package["yomihime_game_link_sdk/py.typed"], authenticated)
        self.assertNotEqual(
            package["yomihime_game_link_sdk/py.typed"],
            b"replaced after digest snapshot\n",
        )

    def test_zip_uses_the_runtime_bytes_captured_by_its_validated_snapshot(
        self,
    ) -> None:
        repository = self.temp_root / "snapshot-plugin-root"
        repository.mkdir()
        for name in ROOT_FILES:
            (repository / name).write_bytes((ROOT / name).read_bytes())
        for name in RUNTIME_DIRS:
            (repository / name).mkdir()
        _write_operator_script_fixtures(repository)
        shutil.copytree(
            ROOT / "modules" / "ff14",
            repository / "modules" / "ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for relative in zip_builder.SHELL_FILES:
            for page in ("00-game-link",):
                if page == "management" and relative in {
                    "index.html",
                    "app.js",
                    "styles.css",
                }:
                    continue
                target = repository / "pages" / page / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "pages" / page / relative, target)
        runtime_file = repository / "core" / "snapshot.py"
        original = b"trusted snapshot bytes\n"
        runtime_file.write_bytes(original)
        archive_path = self.temp_root / "snapshot.zip"
        archive_path.write_bytes(b"previous validated archive")
        read_snapshot = zip_builder._read_snapshot
        replaced = False

        def read_then_modify(
            path, allowed_root=None, label="Package input", max_bytes=None
        ):
            nonlocal replaced
            snapshot = read_snapshot(path, allowed_root, label, max_bytes=max_bytes)
            if Path(path) == runtime_file and not replaced:
                runtime_file.write_bytes(b"changed after snapshot\n")
                replaced = True
            return snapshot

        with (
            mock.patch.object(
                zip_builder, "_read_snapshot", side_effect=read_then_modify
            ),
            # This synthetic tree is intentionally not a Git checkout. Source
            # provenance has separate real-checkout tests; byte capture is real.
            mock.patch(
                "scripts.release_envelope.subprocess.check_output",
                side_effect=["synthetic-source", ""],
            ),
            self.assertRaisesRegex(
                ValueError, "Core source changed before publication"
            ),
        ):
            zip_builder.build(
                archive_path=archive_path,
                repository_root=repository,
                signing_key=Ed25519PrivateKey.generate(),
            )
        self.assertTrue(replaced)
        self.assertEqual(archive_path.read_bytes(), b"previous validated archive")
        self.assertEqual(runtime_file.read_bytes(), b"changed after snapshot\n")

    def test_zip_contains_pinned_sdk_and_root_documents(self) -> None:
        package = {
            "yomihime_game_link_sdk/" + path.name: path.read_bytes()
            for path in (ROOT / "yomihime_game_link_sdk").iterdir()
            if path.suffix == ".py" or path.name == "py.typed"
        }
        archive_path = self.temp_root / "assembled.zip"
        # Runtime payload is the closed vendored graph, not the optional wheel.
        with mock.patch.object(
            zip_builder,
            "validate_wheel",
            side_effect=AssertionError("production ZIP requested an optional wheel"),
        ):
            build(archive_path=archive_path, signing_key=Ed25519PrivateKey.generate())
        projected = zip_builder.projected_page_assets(ROOT)
        with zipfile.ZipFile(archive_path) as schema_archive:
            for config_asset in (
                "_conf_schema.json",
                "modules/ff14/config.py",
                *PAGE_FILES,
                *MAINTENANCE_HELPER_FILES,
            ):
                self.assertEqual(
                    schema_archive.read(config_asset),
                    projected.get(config_asset, (ROOT / config_asset).read_bytes()),
                )
        document_bytes = {
            name: (ROOT / name).read_bytes() for name in ("README.md", "CHANGELOG.md")
        }
        document_text = {
            name: data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            for name, data in document_bytes.items()
        }
        with zipfile.ZipFile(archive_path) as archive:
            self.assertFalse(
                any(name.startswith("pages/ff14/") for name in archive.namelist())
            )
            for name, data in package.items():
                self.assertEqual(archive.read(name), data, name)
            for name, data in document_bytes.items():
                self.assertEqual(archive.read(name), data, name)
            extracted = self.temp_root / "dash-upload-arbitrary-name"
            archive.extractall(extracted)
        for name, data in document_bytes.items():
            document = extracted / name
            self.assertEqual(document.read_bytes(), data, name)
            self.assertEqual(document.read_text(encoding="utf-8"), document_text[name])
        with zipfile.ZipFile(archive_path) as archive:
            raw = archive.read("release.json")
            release = parse_release(raw)
            self.assertEqual(
                {row["path"] for row in release["files"]},
                set(archive.namelist()) - {"release.json", "release.sig.json"},
            )
            bundle = RuntimeBundleStore().capture(extracted, release, kind="core")
            self.addCleanup(bundle.release)
            self.assertEqual(
                validate_core_release(raw, archive.read("release.sig.json"), bundle)[
                    "role"
                ],
                "core",
            )
        bundle.mount_core()
        private = importlib.import_module(bundle.namespace + ".yomihime_game_link_sdk")
        canonical = importlib.import_module("yomihime_game_link_sdk")
        self.assertIsNot(private.ConfigSnapshot, canonical.ConfigSnapshot)
        self.assertEqual(
            bundle.read_resource("yomihime_game_link_sdk/services.py"),
            package["yomihime_game_link_sdk/services.py"],
        )

        incomplete_repository = self.temp_root / "missing-readme-plugin-root"
        incomplete_repository.mkdir()
        for name in ROOT_FILES:
            (incomplete_repository / name).write_bytes(b"fixture")
        for name in RUNTIME_DIRS:
            (incomplete_repository / name).mkdir()
        (incomplete_repository / "README.md").unlink()
        incomplete_archive = self.temp_root / "missing-readme.zip"
        with self.assertRaises(FileNotFoundError):
            build(
                archive_path=incomplete_archive,
                repository_root=incomplete_repository,
                signing_key=Ed25519PrivateKey.generate(),
            )
        self.assertFalse(incomplete_archive.exists())

    def test_builder_rejects_modified_wheel_even_with_a_self_reported_digest(
        self,
    ) -> None:
        tampered = self.temp_root / "self-reported-wheel.whl"
        tampered.write_bytes(self.wheel.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "independently pinned"):
            validate_wheel(tampered)

    def test_rebuilt_wheel_digest_matches_k_review(self) -> None:
        self.assertEqual(
            hashlib.sha256(self.wheel.read_bytes()).hexdigest(), EXPECTED_WHEEL_SHA256
        )


if __name__ == "__main__":
    unittest.main()
