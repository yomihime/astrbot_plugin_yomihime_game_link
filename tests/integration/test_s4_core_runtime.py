"""Actual installed SDK / native Core assembly in an isolated child process."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from ygl_test_subject.scripts.build_dashboard_zip import validate_wheel
from ygl_test_subject.scripts.build_release import build_sdk_wheel

ROOT = Path(__file__).resolve().parents[2]


class CoreRuntimeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work_input = os.environ.get("YGL_TEST_S4_WORK_ROOT")
        cls.temp = tempfile.TemporaryDirectory(
            prefix="ygl-core-integration-", dir=work_input
        )
        cls.work = Path(cls.temp.name).resolve()
        cls.addClassCleanup(cls.temp.cleanup)
        wheel_input = os.environ.get("YGL_TEST_SDK_WHEEL")
        cls.wheel = (
            Path(wheel_input).resolve(strict=True)
            if wheel_input
            else build_sdk_wheel(cls.work / "build", ROOT)
        )
        validate_wheel(cls.wheel)

    def test_minimal_current_wheel_native_core_start_invoke_close(self):
        self._run_core("minimal")

    def test_expanded_installed_sample_actual_core_contracts(self):
        self._run_core("expanded")

    def test_stale_module_services_remain_revoked_after_real_restore(self):
        self._run_core("stale")

    def test_fresh_core_restores_enabled_sample_and_persistent_state(self):
        self._run_core("restart")

    def test_native_negative_contracts_and_management_boundaries(self):
        self._run_core("negative")

    def test_subscription_without_trusted_host_gate_is_denied(self):
        self._run_core("nogate")

    def _run_core(self, mode):
        selected_wheel = self.wheel
        # The production validator checks the pinned digest, metadata and RECORD.
        validate_wheel(selected_wheel)
        expected_sha = hashlib.sha256(selected_wheel.read_bytes()).hexdigest()
        run = self.work / "run" / (mode + "-" + uuid.uuid4().hex[:8])
        plugin = run / "deployment/plugin"
        plugin.mkdir(parents=True)
        copied = {}
        for name in (
            "core",
            "services",
            "infrastructure",
            "extensions",
            "presentation",
            "adapters",
            "scripts",
        ):
            for source in (ROOT / name).rglob("*"):
                if (
                    source.is_file()
                    and source.suffix in {".py", ".sql"}
                    and "__pycache__" not in source.parts
                ):
                    relative = source.relative_to(ROOT)
                    destination = plugin / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    copied[relative.as_posix()] = hashlib.sha256(
                        source.read_bytes()
                    ).hexdigest()
        for name in ("__init__.py", "main.py"):
            shutil.copyfile(ROOT / name, plugin / name)
            copied[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        (run / "sdk-artifact.json").write_text(
            json.dumps(
                {
                    "wheel": str(selected_wheel),
                    "sha256": expected_sha,
                    "guard": "current strict release",
                }
            ),
            encoding="utf-8",
        )
        (run / "source-hashes.json").write_text(
            json.dumps(copied, indent=2), encoding="utf-8"
        )
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        install = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                "--no-cache-dir",
                "--target",
                str(plugin),
                str(selected_wheel),
            ],
            cwd=run,
            env=environment,
            capture_output=True,
            timeout=60,
        )
        (run / "install.stdout").write_bytes(install.stdout)
        (run / "install.stderr").write_bytes(install.stderr)
        self.assertEqual(install.returncode, 0, str(run))
        external = run / "deployment/extensions/offline_sample"
        external.mkdir(parents=True)
        with zipfile.ZipFile(selected_wheel) as wheel:
            for name in ("module.py", "manifest.json", "README.md"):
                destination = (
                    "yomihime.manifest.json" if name == "manifest.json" else name
                )
                external.joinpath(destination).write_bytes(
                    wheel.read(
                        "yomihime_game_link_sdk/_examples/offline_sample/" + name
                    )
                )
        for name in ("module.py", "manifest.json", "README.md"):
            destination = "yomihime.manifest.json" if name == "manifest.json" else name
            self.assertEqual(
                (ROOT / "examples/offline_sample" / name).read_bytes(),
                (external / destination).read_bytes(),
            )
        harness = run / "harness"
        harness.mkdir()
        script = harness / "s4_core_host.py"
        shutil.copyfile(Path(__file__).with_name("s4_core_host.py"), script)
        with (
            (run / "core.stdout").open("wb") as stdout,
            (run / "core.stderr").open("wb") as stderr,
        ):
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(script),
                    str(run / "deployment"),
                    mode,
                ],
                cwd=harness,
                env=environment,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                exit_code = child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                self.fail("process timeout (not business timeout): " + str(run))
        (run / "child.json").write_text(
            json.dumps({"pid": child.pid, "exit": exit_code, "handles_closed": True}),
            encoding="utf-8",
        )
        self.assertEqual(
            exit_code,
            0,
            str(run) + "\n" + (run / "core.stderr").read_text(encoding="utf-8"),
        )
        self.assertIn(
            mode.upper() + "_PASS", (run / "core.stdout").read_text(encoding="utf-8")
        )
