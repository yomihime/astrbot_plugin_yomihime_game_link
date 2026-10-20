"""Enforce real module imports, including relative and dynamic escape paths."""

import ast
import unittest
from pathlib import Path

import yomihime_game_link_sdk as public_sdk

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = {
    "core",
    "services",
    "infrastructure",
    "adapters",
    "extensions",
    "presentation",
}


DEPLOYED_ROOTS = {"astrbot_plugin_yomihime_game_link", "ygl_test_subject", ROOT.name}
SDK = "yomihime_game_link_sdk"
SDK_EXPORTS = set(public_sdk.__all__)
SDK_LEAVES = {
    "cards",
    "contexts",
    "declarations",
    "display",
    "errors",
    "results",
    "services",
    "storage",
    "subscriptions",
    "version",
}


def violations(path, source):
    tree = ast.parse(source)
    found = []
    parts = path.relative_to(ROOT).with_suffix("").parts
    package = parts[:-1]
    service_names = {"services", "scope", "bound", "context", "invocation"}
    dynamic_names = {"__import__"}
    # Track ordinary local aliases, including imports of importlib functions.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id in service_names
        ):
            service_names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        if isinstance(node, ast.ImportFrom) and node.module in {
            "importlib",
            "builtins",
        }:
            dynamic_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in {"import_module", "util", "__import__"}
            )
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Import):
            targets = [alias.name.split(".") for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            prefix = (
                list(package[: max(0, len(package) - node.level + 1)])
                if node.level
                else []
            )
            base = prefix + (node.module.split(".") if node.module else [])
            targets = [base] + [
                base + alias.name.split(".")
                for alias in node.names
                if alias.name != "*"
            ]
        for target in targets:
            if not target:
                continue
            sdk_public = target[0] == SDK and (
                len(target) == 1
                or len(target) == 2
                and target[1] in SDK_LEAVES
                or isinstance(node, ast.ImportFrom)
                and (
                    node.module == SDK
                    and len(target) == 2
                    and target[1] in SDK_EXPORTS
                    or node.module in {SDK + "." + leaf for leaf in SDK_LEAVES}
                    and len(target) == 3
                    and target[2] in SDK_EXPORTS
                )
            )
            if sdk_public:
                continue
            local = target[:2] == list(package[:2])
            if (
                target[0] in FORBIDDEN
                or any(root in target for root in DEPLOYED_ROOTS)
                or target[0] == SDK
                or not local
                and any(part in FORBIDDEN for part in target)
            ):
                found.append((node.lineno, "implementation import"))
        if (
            isinstance(node, ast.ImportFrom)
            and node.level > len(package)
            and node.module is None
            and any(alias.name == "*" for alias in node.names)
        ):
            found.append((node.lineno, "root wildcard import"))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "setattr", "vars"}
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in service_names
            and (
                node.func.id == "vars"
                or len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value.startswith("_")
            )
        ):
            found.append((node.lineno, "private service reflection"))
        if isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name)
            and node.func.id in dynamic_names
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in {"import_module", "spec_from_file_location"}
        ):
            found.append((node.lineno, "dynamic module loader"))
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and isinstance(node.value, ast.Name)
            and node.value.id in service_names
        ):
            found.append((node.lineno, "private service state"))
    return found


class ModuleDependencyBoundaryTests(unittest.TestCase):
    def test_production_modules_use_public_contract_only(self):
        failures = {
            str(path.relative_to(ROOT)): violations(
                path, path.read_text(encoding="utf-8")
            )
            for path in (ROOT / "modules").rglob("*.py")
        }
        self.assertEqual({path: value for path, value in failures.items() if value}, {})

    def test_relative_absolute_dynamic_and_private_escape_are_detected(self):
        path = ROOT / "modules" / "sample" / "module.py"
        for source in (
            "from ...services import cache",
            "from ... import core",
            "from ... import services as svc",
            "from astrbot_plugin_yomihime_game_link.core import ports",
            "from astrbot_plugin_yomihime_game_link import infrastructure as infra",
            "from astrbot_plugin_yomihime_game_link.services import cache as accelerator",
            "import astrbot_plugin_yomihime_game_link.adapters.astrbot as host",
            "import infrastructure.http",
            "from ygl_test_subject.core import ports",
            "__import__('core')",
            "importlib.import_module('services')",
            "services._repository",
            "from renamed_plugin.core import ports",
            "from ... import *",
            "from builtins import __import__ as load; load('core')",
            "svc=services; svc._repository",
            "getattr(services,'_repository')",
            "from yomihime_game_link_sdk import _implementation",
            "from yomihime_game_link_sdk.services import _private",
            "import yomihime_game_link_sdk.services.internal",
        ):
            with self.subTest(source=source):
                self.assertTrue(violations(path, source))
        self.assertFalse(
            violations(
                path,
                "from yomihime_game_link_sdk import ModuleServices\nfrom .features import query",
            )
        )
