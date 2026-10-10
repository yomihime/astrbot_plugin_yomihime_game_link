"""B05 R bounded installed-artifact to E static-discovery preflight."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.fixtures.b05_runtime import (
    EXPECTED_WHEEL_SHA256,
    build_and_install_pinned_sdk,
    run_installed_artifact_static_scan,
)


class B05InstalledArtifactDiscoveryTests(unittest.TestCase):
    def test_pinned_installed_examples_are_discovered_inertly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="b05-r-offline-") as temporary:
            work = Path(temporary)
            installed = build_and_install_pinned_sdk(work)
            self.assertEqual(installed.sha256, EXPECTED_WHEEL_SHA256)

            # The acceptance driver supplies an owned TMP on the qualified
            # Windows filesystem for this disposable scanner root.
            extension_root = work / "extension-root"
            extension_root.mkdir()
            summary = run_installed_artifact_static_scan(installed, extension_root)
            if isinstance(summary, dict) and "unsupported" in summary:
                self.skipTest(
                    "E scanner is unsupported on this runtime: "
                    f"{summary['unsupported']}"
                )

            self.assertEqual(
                {item["package_id"] for item in summary},
                {"empty_module", "offline_sample"},
            )
            by_id = {item["package_id"]: item for item in summary}
            for candidate in summary:
                self.assertEqual(candidate["state"], "disabled")
                self.assertFalse(candidate["enabled"])
                self.assertEqual(candidate["reason_code"], "not_enabled")

            empty_module = by_id["empty_module"]["modules"]
            sample_module = by_id["offline_sample"]["modules"]
            self.assertEqual(empty_module, [])
            self.assertEqual(
                sample_module,
                [
                    {
                        "module_id": "status",
                        "capabilities": 14,
                        "commands": 14,
                        "tools": 3,
                        "config_fields": 3,
                        "schedules": 2,
                        "subscriptions": 2,
                    },
                    {
                        "module_id": "source",
                        "capabilities": 1,
                        "commands": 1,
                        "tools": 0,
                        "config_fields": 0,
                        "schedules": 0,
                        "subscriptions": 0,
                    },
                ],
            )

            expected_files = {
                f"{package}/{filename}"
                for package in ("empty_module", "offline_sample")
                for filename in ("README.md", "module.py", "yomihime.manifest.json")
            }
            self.assertEqual(
                {
                    path.relative_to(extension_root).as_posix()
                    for path in extension_root.rglob("*")
                    if path.is_file()
                },
                expected_files,
            )


if __name__ == "__main__":
    unittest.main()
