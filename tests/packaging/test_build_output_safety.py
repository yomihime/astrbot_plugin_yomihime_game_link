"""Build destinations cannot overwrite inputs or follow linked output files."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_dashboard_zip as dashboard
from scripts import build_release as release


class BuildOutputSafetyTests(unittest.TestCase):
    def test_real_ff14_projection_executes_readers_and_cleans_imports(self):
        before_path = list(sys.path)
        before_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "yomihime_game_link_sdk"
            or name.startswith(("yomihime_game_link_sdk.", "_ygl_release_build"))
        }
        original_readers = dashboard._bundle_readers
        invocations = []
        fail_after_read = False

        def checked_readers(namespace):
            parse, read = original_readers(namespace)

            def checked_read(root, manifest):
                sdk = sys.modules["yomihime_game_link_sdk.declarations"]
                self.assertIs(type(manifest), sdk.PackageManifest)
                self.assertIs(read.__globals__["PackageManifest"], sdk.PackageManifest)
                self.assertIn(namespace, sys.modules)
                resources = read(root, manifest)
                invocations.append(root)
                if fail_after_read:
                    raise ValueError(
                        "injected after actual manifest/resource validation"
                    )
                return resources

            return parse, checked_read

        with patch.object(dashboard, "_bundle_readers", side_effect=checked_readers):
            assets = dashboard.projected_page_assets(dashboard.ROOT)
            fail_after_read = True
            with self.assertRaisesRegex(ValueError, "injected after actual"):
                dashboard.projected_page_assets(dashboard.ROOT)
        self.assertEqual(len(invocations), 2)
        self.assertEqual(sys.path, before_path)
        after_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "yomihime_game_link_sdk"
            or name.startswith(("yomihime_game_link_sdk.", "_ygl_release_build"))
        }
        self.assertEqual(after_modules, before_modules)
        self.assertIn("pages/00-game-link/module-loader.js", assets)
        for name, content in assets.items():
            if "/module-assets/ff14/ff14/" in name:
                relative = name.split("/module-assets/ff14/ff14/", 1)[1]
                self.assertEqual(
                    content, (dashboard.ROOT / "modules/ff14" / relative).read_bytes()
                )

    def test_release_reuses_same_path_wheel_without_copy_or_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "metadata.yaml").write_text("name: sample\nversion: v0.1.2\n")
            out = root / "dist"
            out.mkdir()
            wheel = out / "yomihime_game_link_sdk-0.1.0a11-py3-none-any.whl"
            wheel.write_bytes(b"verified wheel fixture")
            before = wheel.stat()

            def build_zip(source, destination, repository):
                self.assertEqual(source, wheel)
                destination.write_bytes(b"plugin fixture")

            with (
                patch.object(release, "validate_wheel", return_value={}) as validate,
                patch.object(release, "build_dashboard_zip", side_effect=build_zip),
                patch.object(release.shutil, "copyfile") as copy,
            ):
                archive, returned_wheel, checksums = release.build_release(
                    output_dir=out, sdk_wheel=wheel, repository_root=root
                )
            validate.assert_called_with(wheel)
            copy.assert_not_called()
            self.assertEqual(returned_wheel, wheel)
            self.assertEqual(wheel.read_bytes(), b"verified wheel fixture")
            self.assertEqual(wheel.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(wheel.stat().st_ino, before.st_ino)
            expected = "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in sorted((archive, wheel), key=lambda path: path.name)
            )
            self.assertEqual(checksums.read_text(), expected)

    def test_same_path_wheel_exception_rejects_hardlink_without_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "metadata.yaml").write_text("name: sample\nversion: v0.1.2\n")
            wheel = root / "yomihime_game_link_sdk-0.1.0a11-py3-none-any.whl"
            wheel.write_bytes(b"retained sentinel")
            os.link(wheel, root / "other-link")
            with self.assertRaisesRegex(ValueError, "hard link"):
                release.build_release(
                    output_dir=root, sdk_wheel=wheel, repository_root=root
                )
            self.assertEqual(wheel.read_bytes(), b"retained sentinel")
            self.assertFalse((root / "sample-v0.1.2.zip").exists())
            self.assertFalse((root / "SHA256SUMS").exists())

    def test_missing_frontend_projection_has_actionable_prerequisite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in dashboard.ROOT_FILES:
                (root / name).write_bytes(b"synthetic root input")
            with self.assertRaisesRegex(
                FileNotFoundError, "npm run build --prefix pages/frontend"
            ):
                dashboard.included_files(root)

    def test_failed_archive_capture_preserves_previous_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Real included_files returns canonical paths, even when Windows
            # TEMP names the same directory through its 8.3 short alias.
            root = Path(temporary).resolve(strict=True)
            source = root / "runtime.py"
            source.write_bytes(b"source")
            artifact = root / "previous.zip"
            artifact.write_bytes(b"previous artifact")
            with (
                patch.object(dashboard, "validate_wheel", return_value={}),
                patch.object(dashboard, "included_files", return_value=[source]),
                patch.object(dashboard, "projected_page_assets", return_value={}),
                patch.object(
                    dashboard, "_read_snapshot", side_effect=OSError("capture failed")
                ),
                self.assertRaisesRegex(OSError, "capture failed"),
            ):
                dashboard.build(root / "sdk.whl", artifact, root)
            self.assertEqual(artifact.read_bytes(), b"previous artifact")

    def test_output_parent_junction_is_rejected_without_target_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            linked = root / "linked"
            if os.name == "nt":
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                linked.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink, junction, or reparse"):
                dashboard.publish_artifacts({linked / "archive.zip": b"new"})
            self.assertEqual(list(outside.iterdir()), [])

    def test_dashboard_rejects_input_destination_before_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "runtime.py"
            source.write_bytes(b"preserve source")
            with (
                patch.object(dashboard, "validate_wheel", return_value={}),
                patch.object(dashboard, "included_files", return_value=[source]),
                patch.object(dashboard, "projected_page_assets", return_value={}),
                self.assertRaisesRegex(ValueError, "input"),
            ):
                dashboard.build(root / "sdk.whl", source, root)
            self.assertEqual(source.read_bytes(), b"preserve source")

    def test_release_preflights_entire_output_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "metadata.yaml").write_text("name: sample\nversion: v0.1.2\n")
            wheel = root / "yomihime_game_link_sdk-0.1.0a11-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            out = root / "out"
            out.mkdir()
            zip_output = out / "sample-v0.1.2.zip"
            zip_output.write_bytes(b"previous zip")
            outside = root / "sentinel"
            outside.write_bytes(b"preserve outside")
            os.link(outside, out / "SHA256SUMS")
            with (
                patch.object(release, "validate_wheel", return_value={}),
                patch.object(release, "build_dashboard_zip") as builder,
                self.assertRaisesRegex(ValueError, "hard link"),
            ):
                release.build_release(
                    output_dir=out, sdk_wheel=wheel, repository_root=root
                )
            builder.assert_not_called()
            self.assertEqual(zip_output.read_bytes(), b"previous zip")
            self.assertEqual(outside.read_bytes(), b"preserve outside")

    def test_checksums_reject_artifact_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact"
            artifact.write_bytes(b"preserve artifact")
            with self.assertRaisesRegex(ValueError, "input"):
                release.write_checksums((artifact,), artifact)
            self.assertEqual(artifact.read_bytes(), b"preserve artifact")

    def test_dotted_checkout_imports_without_path_or_module_pollution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout.with.dot"
            (root / "scripts").mkdir(parents=True)
            # Copy only trusted validators/runtime declarations, not generated assets.
            source = dashboard.ROOT
            for directory in (
                "adapters",
                "core",
                "extensions",
                "services",
                "infrastructure",
                "presentation",
                "yomihime_game_link_sdk",
            ):
                shutil.copytree(
                    source / directory,
                    root / directory,
                    ignore=shutil.ignore_patterns("__pycache__", "assets"),
                )
            shutil.copyfile(source / "__init__.py", root / "__init__.py")
            shutil.copyfile(
                source / "scripts/build_dashboard_zip.py",
                root / "scripts/build_dashboard_zip.py",
            )
            script = """import runpy, sys
tool=runpy.run_path(sys.argv[1])
before=list(sys.path)
modules=set(sys.modules)
from pathlib import Path
assets=tool['projected_page_assets'](Path(sys.argv[2]))
assert 'pages/00-game-link/module-loader.js' in assets
assert sys.path == before
assert not any(name.startswith('_ygl_release_build') for name in set(sys.modules)-modules)
assert not any(name == 'yomihime_game_link_sdk' or name.startswith('yomihime_game_link_sdk.') for name in set(sys.modules)-modules)
"""
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    script,
                    str(root / "scripts/build_dashboard_zip.py"),
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
