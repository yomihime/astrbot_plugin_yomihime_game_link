"""Canonical SDK declarations, identity and import isolation."""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import unittest
from pathlib import Path
from typing import get_type_hints

import yomihime_game_link_sdk as sdk

ROOT = Path(__file__).resolve().parents[2]
LEAVES = (
    "contexts",
    "declarations",
    "display",
    "results",
    "storage",
    "services",
    "subscriptions",
    "errors",
    "version",
)


class PublicExportTests(unittest.TestCase):
    def test_public_web_origin_and_policy_round_trip_at_canonical_root(self):
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

    def test_canonical_release_and_abi_are_independent(self):
        self.assertEqual(sdk.__version__, "0.1.0a6")
        self.assertEqual(sdk.MODULE_ABI_VERSION, "2.0")
        self.assertIs(sdk.PrivacyFloor.OWNER, sdk.PrivacyFloor("owner"))

    def test_all_public_declarations_are_unique_and_exported(self):
        self.assertEqual(len(sdk.__all__), len(set(sdk.__all__)))
        declared = set()
        for leaf in LEAVES:
            module = importlib.import_module("yomihime_game_link_sdk." + leaf)
            for name, value in vars(module).items():
                if (
                    isinstance(value, type)
                    and value.__module__ == module.__name__
                    and not name.startswith("_")
                ):
                    declared.add(name)
        self.assertLessEqual(declared, set(sdk.__all__))
        for name in sdk.__all__:
            self.assertTrue(hasattr(sdk, name), name)

    def test_only_module_contracts_are_reexported(self):
        for name in (
            "CacheRepository",
            "PersistedConfigPatch",
            "ModuleStoragePaths",
            "AdminOperations",
            "TrustedConversationResolver",
            "SubscriptionRecord",
            "DeliveryEvent",
            "DueCollectionJob",
            "freeze_json",
            "validate_parameters",
            "PrincipalView",
            "ConfigFieldDescriptor",
            "SourceDescriptor",
            "SourceSpec",
        ):
            self.assertFalse(hasattr(sdk, name), name)
        self.assertNotIn("storage", sdk.ModuleServices.__dataclass_fields__)

    def test_retired_same_shape_helpers_absent(self):
        from ygl_test_subject.services import cache

        self.assertFalse(hasattr(sdk.OwnerScope, "private"))
        self.assertEqual(
            sdk.OwnerScope.authorized("user", sdk.GrantReference("grant", 1)).kind,
            sdk.OwnershipKind.AUTHORIZED,
        )
        self.assertFalse(hasattr(cache, "CacheService"))
        self.assertNotIn("CacheService", cache.__all__)
        self.assertTrue(hasattr(cache, "CacheAccessCoordinator"))
        for name in (
            "ModuleUnloadReceipt",
            "OrdinaryRollbackReceipt",
            "ManagementRecoveryProjection",
        ):
            self.assertFalse(hasattr(sdk, name))
        self.assertNotIn("administration", sdk.ModuleServices.__dataclass_fields__)

    def test_sdk_imports_are_stdlib_or_canonical_sdk_only(self):
        for source in (ROOT / "yomihime_game_link_sdk").rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        self.assertLessEqual(node.level, 1)
                        continue
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertIn(
                        name.split(".")[0],
                        sys.stdlib_module_names
                        | {"__future__", "yomihime_game_link_sdk"},
                        (source, name),
                    )


class AnnotationIntegrationTests(unittest.TestCase):
    def test_public_contract_annotations_resolve(self):
        for leaf in LEAVES:
            module = importlib.import_module("yomihime_game_link_sdk." + leaf)
            for name, value in vars(module).items():
                if getattr(value, "__module__", None) != module.__name__:
                    continue
                if inspect.isfunction(value) or inspect.isclass(value):
                    with self.subTest(module=leaf, name=name):
                        get_type_hints(value)
                if inspect.isclass(value):
                    for method_name, method in vars(value).items():
                        if isinstance(method, property):
                            method = method.fget
                        if isinstance(method, classmethod):
                            method = method.__func__
                        if inspect.isfunction(method):
                            with self.subTest(
                                module=leaf, name=name, method=method_name
                            ):
                                get_type_hints(method)


class CanonicalAPIIdentityTests(unittest.TestCase):
    def test_core_and_module_use_the_same_public_types(self):
        from ygl_test_subject.core import ports
        from ygl_test_subject.core.contracts import results, services, storage

        self.assertIs(results.CapabilityResult, sdk.CapabilityResult)
        self.assertIs(services.ModuleServices, sdk.ModuleServices)
        self.assertIs(storage.OwnerScope, sdk.OwnerScope)
        self.assertIs(ports.RevisionConflict, sdk.RevisionConflict)
        self.assertIs(ports.UniqueConstraintViolation, sdk.UniqueConstraintViolation)
        self.assertIs(services.SourceHttpError, sdk.SourceHttpError)

    def test_canonical_types_have_one_definition_location(self):
        self.assertEqual(
            sdk.CapabilityResult.__module__, "yomihime_game_link_sdk.results"
        )
        self.assertEqual(
            sdk.ModuleServices.__module__, "yomihime_game_link_sdk.services"
        )
        self.assertEqual(
            sdk.SourceHttpError.__module__, "yomihime_game_link_sdk.errors"
        )
