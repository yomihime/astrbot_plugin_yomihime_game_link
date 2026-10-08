"""The SDK root is the one documented, host-independent public import path."""

from __future__ import annotations

import ast
import importlib
import inspect
import unittest
from pathlib import Path
from typing import get_type_hints

ROOT = Path(__file__).resolve().parents[2]


class PublicExportTests(unittest.TestCase):
    def test_public_web_origin_and_policy_round_trip_at_canonical_root(self) -> None:
        import yomihime_sdk as sdk

        self.assertIs(
            sdk.InvocationOrigin("web_public"), sdk.InvocationOrigin.WEB_PUBLIC
        )
        self.assertIs(
            sdk.InvocationPolicy("command_and_public_web"),
            sdk.InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
        )
        self.assertIs(
            sdk.InvocationPolicy("command_only"), sdk.InvocationPolicy.COMMAND_ONLY
        )
        self.assertTrue(sdk.is_compatible_contract_version("1.5.0"))

    def test_all_api_declarations_are_listed_at_the_sdk_root(self) -> None:
        sdk_tree = ast.parse(
            (ROOT / "yomihime_sdk/__init__.py").read_text(encoding="utf-8")
        )
        exports = next(
            ast.literal_eval(node.value)
            for node in sdk_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
        )
        self.assertEqual(len(exports), len(set(exports)))
        self.assertTrue(exports)
        self.assertTrue(
            all(name == "__version__" or not name.startswith("_") for name in exports)
        )

        declared = set()
        for path in (ROOT / "yomihime_sdk/api").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(
                    node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    if not node.name.startswith("_"):
                        declared.add(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    declared.update(
                        target.id
                        for target in targets
                        if isinstance(target, ast.Name)
                        and not target.id.startswith("_")
                    )
        self.assertEqual(set(exports) - {"__version__"}, declared)

    def test_owner_floor_and_contract_compatibility_are_public(self) -> None:
        import yomihime_sdk as sdk

        self.assertEqual(sdk.__version__, "1.8.0")
        self.assertEqual(sdk.CONTRACT_VERSION, "1.8.0")
        self.assertEqual(sdk.CONTRACT_REVISION, "MODULE-DISPLAY-01")
        self.assertEqual(
            sdk.COMPATIBLE_CONTRACT_VERSIONS,
            ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0", "1.8.0"),
        )
        self.assertIs(sdk.PrivacyFloor.OWNER, sdk.PrivacyFloor("owner"))
        self.assertTrue(
            all(
                sdk.is_compatible_contract_version(version)
                for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0")
            )
        )

    def test_sdk_source_uses_only_the_canonical_api_package(self) -> None:
        source = (ROOT / "yomihime_sdk/__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        self.assertTrue(imports)
        self.assertTrue(
            all(node.level == 1 and node.module.startswith("api.") for node in imports)
        )


class AnnotationIntegrationTests(unittest.TestCase):
    def test_public_contract_annotations_resolve(self):
        for part in (
            "api.contexts",
            "api.manifests",
            "api.schema",
            "api.display",
            "api.results",
            "api.storage",
            "api.services",
            "api.subscriptions",
            "api.validation",
            "core.ports",
        ):
            module = importlib.import_module("ygl_test_subject." + part)
            for name, obj in vars(module).items():
                if getattr(obj, "__module__", None) != module.__name__:
                    continue
                if inspect.isfunction(obj) or inspect.isclass(obj):
                    with self.subTest(module=part, name=name):
                        get_type_hints(obj)
                if inspect.isclass(obj):
                    for method_name, method in vars(obj).items():
                        if isinstance(method, property):
                            method = method.fget
                        if inspect.isfunction(method):
                            with self.subTest(
                                module=part, name=name, method=method_name
                            ):
                                get_type_hints(method)


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
ROOT = Path(__file__).resolve().parents[2]


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
                    ROOT
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
        from yomihime_sdk import SourceHttpError as FacadeSourceHttpError
        from yomihime_sdk.api import SourceHttpError as PackageSourceHttpError
        from yomihime_sdk.api.results import CapabilityResult
        from yomihime_sdk.api.services import ModuleServices, SourceHttpError

        self.assertIs(FacadeCapabilityResult, CapabilityResult)
        self.assertIs(FacadeModuleServices, ModuleServices)
        self.assertIs(FacadeSourceHttpError, SourceHttpError)
        self.assertIs(PackageSourceHttpError, SourceHttpError)
        self.assertEqual(CapabilityResult.__module__, "yomihime_sdk.api.results")
        self.assertEqual(ModuleServices.__module__, "yomihime_sdk.api.services")
        self.assertEqual(SourceHttpError.__module__, "yomihime_sdk.api.services")


if __name__ == "__main__":
    unittest.main()
