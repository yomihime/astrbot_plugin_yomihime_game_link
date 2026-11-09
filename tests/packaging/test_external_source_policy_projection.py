"""Explicit source-policy capture stays paired, inert and failure atomic."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.static_page_projection import PROJECTION_FILE
from ygl_test_subject.services.external_source_policies import (
    POLICY_FILE,
    ExternalSourcePolicies,
)

from scripts import build_dashboard_zip as dashboard
from scripts import build_release as release
from tests.services.test_external_source_policies import (
    OWNER,
    ROOT,
    source_policy_fixture,
    write_external_package,
)


class ExternalSourcePolicyProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ygl-policy-build-", dir=ROOT
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(strict=True)
        (self.root / "modules").mkdir()
        self.manifest, self.policy = source_policy_fixture()
        self.raw = json.dumps(self.manifest, indent=2).encode("utf-8")
        self.package = write_external_package(self.root, self.raw)
        self.policy_path = self.package / "yomihime.source-policy.json"
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        page = self.root / "pages/00-game-link"
        page.mkdir(parents=True)
        (page / "index.html").write_text(
            '<html data-page-name="00-game-link">'
            '<template id="module-style-assets"></template></html>',
            encoding="utf-8",
        )
        self.runtime_source = self.root / "runtime.py"
        self.runtime_source.write_bytes(b"# synthetic runtime input\n")
        (self.root / "metadata.yaml").write_text(
            "name: synthetic_core\nversion: v0.1.2\n", encoding="utf-8"
        )
        self.wheel = self.root / "yomihime_game_link_sdk-0.1.0a24-py3-none-any.whl"
        self.wheel.write_bytes(b"wheel validation has independent pinned tests")

    def assets(self, *, page_roots=None, policy_roots=None):
        return dashboard.projected_page_assets(
            self.root,
            bundle_roots=(),
            external_page_roots=(self.package,) if page_roots is None else page_roots,
            external_source_policy_roots=(self.package,)
            if policy_roots is None
            else policy_roots,
        )

    def build(self, output):
        # Wheel integrity and the full Core input inventory have separate gates;
        # keep this integration focused on real external capture and publication.
        with (
            patch.object(dashboard, "validate_wheel", return_value={}),
            patch.object(
                dashboard, "included_files", return_value=[self.runtime_source]
            ),
        ):
            return dashboard.build(
                self.wheel,
                output,
                self.root,
                external_page_roots=(self.package,),
                external_source_policy_roots=(self.package,),
            )

    def test_selected_source_policy_is_pinned_to_exact_page_manifest_bytes(self):
        assets = self.assets()
        captured = json.loads(assets[POLICY_FILE])
        self.assertEqual(captured["packages"][0]["manifest"], self.raw.decode("utf-8"))
        self.assertEqual(
            captured["packages"][0]["credentials"], self.policy["credentials"]
        )
        static = json.loads(assets[PROJECTION_FILE])
        self.assertIn(self.raw.decode("utf-8"), static["manifests"])
        selected = ExternalSourcePolicies(assets[POLICY_FILE])
        package = next(
            p for p in discover_packages(self.root) if p.package_id == "minimal"
        )
        self.assertTrue(selected.permits(package))
        self.assertEqual(
            selected.manifests["minimal"], hashlib.sha256(self.raw).hexdigest()
        )
        self.assertIn("example_qr", selected.authorizations[OWNER])

    def test_selected_source_policy_preserves_valid_config_migrations(self):
        self.manifest["modules"][0]["config_fields"].append(
            {
                "name": "history_limit",
                "sensitive": False,
                "required": False,
                "default": 2,
                "description": "Synthetic compatibility floor.",
                "value_schema": {"type": "integer", "minimum": 2},
            }
        )
        self.raw = json.dumps(self.manifest, indent=2).encode("utf-8")
        (self.package / "yomihime.manifest.json").write_bytes(self.raw)
        migration = {
            "owner": OWNER,
            "field": "history_limit",
            "kind": "integer_floor",
            "from_minimum": 1,
            "to_minimum": 2,
        }
        self.policy["config_migrations"] = [migration]
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")

        captured = json.loads(self.assets()[POLICY_FILE])
        self.assertEqual(captured["packages"][0]["config_migrations"], [migration])
        selected = ExternalSourcePolicies(json.dumps(captured).encode("utf-8"))
        self.assertEqual(
            [dict(rule) for rule in selected.config_migrations["minimal"]],
            [migration],
        )

    def test_ordinary_external_page_selection_does_not_grant_source_authority(self):
        assets = self.assets(policy_roots=())
        self.assertIn(PROJECTION_FILE, assets)
        self.assertNotIn(POLICY_FILE, assets)
        self.assertFalse(ExternalSourcePolicies().credential_policies)

    def test_capture_never_imports_external_python_or_packages_credential_material(
        self,
    ):
        sentinel = b"synthetic-secret-must-not-enter-archive"
        (self.package / "credential-material.json").write_bytes(sentinel)
        (self.package / "unselected-private.txt").write_bytes(sentinel)
        assets = self.assets()
        self.assertFalse(any(name.endswith(".py") for name in assets))
        self.assertNotIn(sentinel, b"".join(assets.values()))
        with self.assertRaisesRegex(ValueError, "do not compose external"):
            self.build(self.root / "closed.zip")
        self.assertFalse((self.root / "closed.zip").exists())

    def test_source_policy_requires_explicit_corresponding_external_page_package(self):
        with self.assertRaisesRegex(ValueError, "paired page manifest"):
            self.assets(page_roots=())
        other = self.root / "other-selection"
        other.mkdir()
        other_package = write_external_package(other, self.raw + b"\n")
        with self.assertRaisesRegex(ValueError, "paired page manifest"):
            self.assets(page_roots=(other_package,))

    def test_unknown_owner_and_malformed_policy_preserve_existing_archive(self):
        output = self.root / "previous.zip"
        output.write_bytes(b"previous artifact")
        self.policy["credentials"][0]["owner"] = "another/demo"
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.assets()
        self.assertEqual(output.read_bytes(), b"previous artifact")
        self.policy_path.write_bytes(
            b'{"schema_version":1,"credentials":[],"authorizations":{},"token":"not-allowed"}'
        )
        with self.assertRaises(ValueError):
            self.assets()
        self.assertEqual(output.read_bytes(), b"previous artifact")

    def test_manifest_and_policy_mutation_during_capture_reject_without_publication(
        self,
    ):
        output = self.root / "previous.zip"
        original_read = dashboard._read_snapshot
        for target in (self.package / "yomihime.manifest.json", self.policy_path):
            with self.subTest(target=target.name):
                original = target.read_bytes()
                mutated = False

                def mutate_after_first_read(path, *args, **kwargs):
                    nonlocal mutated
                    data = original_read(path, *args, **kwargs)
                    if Path(path) == target and not mutated:
                        target.write_bytes(data + b"\n")
                        mutated = True
                    return data

                output.write_bytes(b"previous artifact")
                try:
                    with (
                        patch.object(
                            dashboard,
                            "_read_snapshot",
                            side_effect=mutate_after_first_read,
                        ),
                        # The pre-capture raw-policy pin can now detect this
                        # at the final publication fence instead of the inner read.
                        self.assertRaisesRegex(
                            ValueError, "changed (?:during capture|before publication)"
                        ),
                    ):
                        self.assets()
                    self.assertTrue(mutated)
                    self.assertEqual(output.read_bytes(), b"previous artifact")
                finally:
                    target.write_bytes(original)

    def test_external_inputs_are_rejected_before_any_source_is_read(self):
        output = self.root / "previous.zip"
        output.write_bytes(b"previous artifact")
        with (
            patch.object(dashboard, "_read_snapshot") as read,
            self.assertRaisesRegex(ValueError, "do not compose external"),
        ):
            self.build(output)
        read.assert_not_called()
        self.assertEqual(output.read_bytes(), b"previous artifact")

    def test_release_rejects_external_composition_instead_of_requiring_a_pair(self):
        output = self.root / "release"
        with (
            patch.object(release, "validate_wheel", return_value={}),
            self.assertRaisesRegex(ValueError, "do not compose external"),
        ):
            release.build_release(
                output_dir=output,
                sdk_wheel=self.wheel,
                repository_root=self.root,
                external_page_roots=(self.package,),
                external_source_policy_roots=(self.package,),
            )
        self.assertFalse(output.exists())

    def test_release_missing_page_pair_preserves_entire_existing_output_batch(self):
        output = self.root / "release"
        output.mkdir()
        old = {
            output / "synthetic_core-v0.1.2.zip": b"old zip",
            output / self.wheel.name: b"old wheel",
            output / "SHA256SUMS": b"old sums",
        }
        for path, data in old.items():
            path.write_bytes(data)
        with (
            patch.object(release, "validate_wheel", return_value={}),
            patch.object(dashboard, "validate_wheel", return_value={}),
            patch.object(
                dashboard, "included_files", return_value=[self.runtime_source]
            ),
            self.assertRaisesRegex(ValueError, "do not compose external"),
        ):
            release.build_release(
                output_dir=output,
                sdk_wheel=self.wheel,
                repository_root=self.root,
                external_source_policy_roots=(self.package,),
            )
        for path, data in old.items():
            self.assertEqual(path.read_bytes(), data)

    def test_release_cli_refuses_retired_external_composition_flags(self):
        with (
            patch.object(
                sys,
                "argv",
                ["build_release.py", "--external-page-package", str(self.package)],
            ),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            release.main()
        self.assertEqual(raised.exception.code, 2)
