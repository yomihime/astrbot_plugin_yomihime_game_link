"""Distribution pins and actual CLI bootstrap guards, without credential IO."""

import ast
import hashlib
import os
import re
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
CLIS = ("scripts/configure_source_credentials.py", "scripts/admin_credentials.py")
REVIEWED_RELEASE = "0.1.0a6"
REVIEWED_WHEEL_SHA256 = (
    "54d5a0766ae5b626e575ff0d1936b49ce14a7d7f57b6db1f62882653eea9c81e"
)


def _literal(source, name):
    return next(
        ast.literal_eval(node.value)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        )
    )


def _current_release():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf8"))[
        "project"
    ]["version"]


def _consumer_pins(overrides=None):
    overrides = overrides or {}

    def source(path):
        return overrides.get(path, (ROOT / path).read_text(encoding="utf8"))

    pins = {"main.py": {_literal(source("main.py"), "SDK_VERSION")}}
    for path in CLIS:
        pins[path] = {_literal(source(path), "_SDK_VERSION")}
    wheel_name = _literal(source("scripts/build_release.py"), "wheel_name")
    match = re.fullmatch(r"yomihime_game_link_sdk-(.+)-py3-none-any\.whl", wheel_name)
    if match is None:
        raise AssertionError("release wheel filename is not canonical")
    pins["scripts/build_release.py"] = {match.group(1)}
    builder = source("scripts/build_dashboard_zip.py")
    member_versions = {
        name.split(".dist-info/", 1)[0].removeprefix("yomihime_game_link_sdk-")
        for name in _literal(builder, "EXPECTED_WHEEL_ENTRIES")
        if ".dist-info/" in name
    }
    validator = next(
        node
        for node in ast.parse(builder).body
        if isinstance(node, ast.FunctionDef) and node.name == "validate_wheel"
    )
    metadata_versions = {
        node.value.removeprefix("Version: ")
        for node in ast.walk(validator)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("Version: ")
    }
    if not member_versions or not metadata_versions:
        raise AssertionError("Dashboard member or metadata release guard is missing")
    pins["scripts/build_dashboard_zip.py"] = member_versions | metadata_versions
    return pins


def _execute_actual_cli_guard(path, *, release, abi="2.0", foreign=False):
    tree = ast.parse((ROOT / path).read_text(encoding="utf8"))
    start = next(
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_PLUGIN_ROOT"
            for target in node.targets
        )
    )
    end = next(
        index
        for index in range(start, len(tree.body))
        if isinstance(tree.body[index], ast.Try)
    )
    # Execute the exact production import/path/release/ABI guard. SDK imports are
    # controlled preloaded descriptors; Core imports and credential code never run.
    code = compile(
        ast.Module(body=tree.body[start : end + 1], type_ignores=[]), path, "exec"
    )
    package = SimpleNamespace(
        __version__=release,
        __file__=str(
            ROOT.parent / "foreign-sdk/__init__.py"
            if foreign
            else ROOT / "yomihime_game_link_sdk/__init__.py"
        ),
    )
    version = SimpleNamespace(MODULE_ABI_VERSION=abi)
    modules = {
        "yomihime_game_link_sdk": package,
        "yomihime_game_link_sdk.version": version,
    }
    namespace = {
        "__file__": str(ROOT / path),
        "Path": Path,
        "sys": SimpleNamespace(path=[]),
        "importlib": SimpleNamespace(import_module=modules.__getitem__),
    }
    exec(code, namespace)
    return namespace


class SDKReleasePinTests(unittest.TestCase):
    def test_current_consumers_match_distribution_metadata(self):
        expected = _current_release()
        self.assertEqual(expected, REVIEWED_RELEASE)
        for path, pins in _consumer_pins().items():
            with self.subTest(consumer=path):
                self.assertEqual(pins, {expected})

    def test_actual_wheel_literals_and_stale_controlled_pins(self):
        from scripts.build_dashboard_zip import validate_wheel
        from scripts.build_release import build_sdk_wheel

        with tempfile.TemporaryDirectory(prefix="sdk-release-pin-") as temporary:
            explicit = os.environ.get("YGL_TEST_SDK_WHEEL")
            wheel = (
                Path(explicit).resolve(strict=True)
                if explicit
                else build_sdk_wheel(Path(temporary) / "build", ROOT)
            )
            self.assertEqual(
                wheel.name, "yomihime_game_link_sdk-0.1.0a6-py3-none-any.whl"
            )
            self.assertEqual(
                hashlib.sha256(wheel.read_bytes()).hexdigest(), REVIEWED_WHEEL_SHA256
            )
            payload = validate_wheel(wheel)
            actual_manifest = {
                name.removeprefix("yomihime_game_link_sdk/"): hashlib.sha256(
                    data
                ).hexdigest()
                for name, data in payload.items()
            }
            self.assertEqual(len(actual_manifest), 17)
            main_manifest = _literal(
                (ROOT / "main.py").read_text(encoding="utf8"), "SDK_PACKAGE_MANIFEST"
            )
            self.assertEqual(main_manifest, actual_manifest)
            stale_manifest = dict(main_manifest)
            stale_manifest["version.py"] = (
                "86fb9111d50cc877b69aea1f04c9a619e4a4aa685873ea989bca107cb7d31179"
            )
            with self.assertRaises(AssertionError):
                self.assertEqual(stale_manifest, actual_manifest)
            for path in (
                "scripts/build_dashboard_zip.py",
                "tests/fixtures/b05_runtime.py",
                "tests/packaging/test_sdk_artifact_install.py",
            ):
                with self.subTest(consumer=path):
                    current = _literal(
                        (ROOT / path).read_text(encoding="utf8"),
                        "EXPECTED_WHEEL_SHA256",
                    )
                    self.assertEqual(current, REVIEWED_WHEEL_SHA256)
                    stale = "56673512cb4526f24ee502fcce9a5e056591edbe39f4d011f2c0b9f9c76d6d00"
                    self.assertNotEqual(current, stale)
                    with self.assertRaises(AssertionError):
                        self.assertEqual(stale, REVIEWED_WHEEL_SHA256)

    def test_stale_cli_pin_is_detected_in_memory(self):
        expected = _current_release()
        self.assertNotEqual(expected, "0.1.0a3")
        for path in CLIS:
            with self.subTest(consumer=path):
                tree = ast.parse((ROOT / path).read_text(encoding="utf8"))
                assignment = next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "_SDK_VERSION"
                        for target in node.targets
                    )
                )
                assignment.value = ast.Constant("0.1.0a3")
                pins = _consumer_pins({path: ast.unparse(tree)})
                self.assertEqual(
                    {key for key, values in pins.items() if values != {expected}},
                    {path},
                )
                with self.assertRaises(AssertionError):
                    self.assertEqual(pins[path], {expected})

    def test_actual_cli_guards_accept_only_local_current_release_and_abi(self):
        expected = _current_release()
        for path in CLIS:
            with self.subTest(consumer=path, case="local current ABI"):
                try:
                    namespace = _execute_actual_cli_guard(path, release=expected)
                except RuntimeError as failure:
                    self.fail(f"current local SDK was refused: {failure}")
                self.assertEqual(namespace["_SDK_VERSION"], expected)
                self.assertEqual(namespace["_SDK_MODULE_ABI_VERSION"], "2.0")
            for case, arguments in (
                ("old release", {"release": "0.1.0a3"}),
                ("foreign preloaded package", {"release": expected, "foreign": True}),
                (
                    "mixed incompatible version leaf",
                    {"release": expected, "abi": "1.7.0"},
                ),
            ):
                with self.subTest(consumer=path, case=case):
                    with self.assertRaisesRegex(
                        RuntimeError, "^the pinned plugin-local SDK is unavailable$"
                    ) as caught:
                        _execute_actual_cli_guard(path, **arguments)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertTrue(caught.exception.__suppress_context__)
