"""Prove that legacy API imports are one-way re-exports of the SDK API."""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

_API_LEAVES = (
    "__init__",
    "administration",
    "contexts",
    "display",
    "manifests",
    "results",
    "schema",
    "services",
    "storage",
    "subscriptions",
    "validation",
    "version",
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class CanonicalAPIIdentityTests(unittest.TestCase):
    def test_legacy_shims_explicitly_reexport_canonical_objects(self) -> None:
        for leaf in _API_LEAVES:
            with self.subTest(module=leaf):
                legacy_name = (
                    "ygl_test_subject.api"
                    if leaf == "__init__"
                    else f"ygl_test_subject.api.{leaf}"
                )
                canonical_name = (
                    "yomihime_sdk.api"
                    if leaf == "__init__"
                    else f"yomihime_sdk.api.{leaf}"
                )
                legacy = importlib.import_module(legacy_name)
                canonical = importlib.import_module(canonical_name)
                shim_path = (
                    _REPOSITORY_ROOT
                    / "api"
                    / ("__init__.py" if leaf == "__init__" else f"{leaf}.py")
                )
                imports = ast.parse(shim_path.read_text(encoding="utf-8")).body
                aliases = [
                    alias
                    for statement in imports
                    if isinstance(statement, ast.ImportFrom)
                    for alias in statement.names
                ]
                self.assertTrue(aliases, "shim must contain explicit imports")
                self.assertTrue(all(alias.name != "*" for alias in aliases))
                for alias in aliases:
                    exported_name = alias.asname or alias.name
                    if leaf == "__init__" and exported_name == "version":
                        self.assertEqual(
                            getattr(legacy, exported_name).__name__,
                            "ygl_test_subject.api.version",
                        )
                        self.assertIs(
                            legacy.version.CONTRACT_VERSION,
                            canonical.version.CONTRACT_VERSION,
                        )
                        continue
                    self.assertIs(
                        getattr(legacy, exported_name),
                        getattr(canonical, exported_name),
                        exported_name,
                    )

    def test_public_dtos_facade_and_used_private_helpers_share_identity(self) -> None:
        cases = (
            ("results", "CapabilityResult"),
            ("results", "ResultStatus"),
            ("services", "ModuleServices"),
            ("services", "ModuleHandlers"),
            ("manifests", "PackageManifest"),
            ("display", "DisplayDocument"),
            ("display", "TextBlock"),
            ("display", "_asset"),
            ("validation", "_validate"),
        )
        for module_name, symbol_name in cases:
            with self.subTest(module=module_name, symbol=symbol_name):
                legacy = importlib.import_module(f"ygl_test_subject.api.{module_name}")
                canonical = importlib.import_module(f"yomihime_sdk.api.{module_name}")
                self.assertIs(
                    getattr(legacy, symbol_name), getattr(canonical, symbol_name)
                )

        from yomihime_sdk import CapabilityResult as FacadeCapabilityResult
        from yomihime_sdk import ModuleServices as FacadeModuleServices
        from yomihime_sdk.api.results import CapabilityResult
        from yomihime_sdk.api.services import ModuleServices

        self.assertIs(FacadeCapabilityResult, CapabilityResult)
        self.assertIs(FacadeModuleServices, ModuleServices)
        self.assertEqual(CapabilityResult.__module__, "yomihime_sdk.api.results")
        self.assertEqual(ModuleServices.__module__, "yomihime_sdk.api.services")


if __name__ == "__main__":
    unittest.main()
