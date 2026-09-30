"""AstrBot entry point with a content-pinned, plugin-local SDK bootstrap."""

from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import sys
from pathlib import Path
from types import ModuleType

SDK_VERSION = "1.3.0"
SDK_CONTRACT_REVISION = "FF14-W1-P1"
SDK_COMPATIBLE_CONTRACT_VERSIONS = ("1.0.0", "1.1.0", "1.2.0", "1.3.0")

SDK_PACKAGE_MANIFEST = {
    "__init__.py": "427e3a6d04703b37cbde3664eb0e39e3f5b986a57c02fd315424e334d34d57e9",
    "_examples/empty_module/README.md": "54f44691207b27a1e6ee818cd55e14c30086cd3c685176e470b147ffd5203957",
    "_examples/empty_module/manifest.json": "27ed02b89a99fbea400dd75a1a405db27929f31acb4ceb582b83e8af1e61b1a8",
    "_examples/empty_module/module.py": "231518c6a6db052dcadfd0bd3578ff41f5dcda2d9879808730cbecab74734ce1",
    "_examples/offline_sample/README.md": "26fac4a0f4a6700c514295cf5bc915a86f34c8409ce01a75e44a9219b5e8f336",
    "_examples/offline_sample/manifest.json": "2a86197ea2f91ece1099395b3f0e74f4097145a14127842b5f53f652e79f10cb",
    "_examples/offline_sample/module.py": "df6c58755c5af19c5c9035b45fa94ae3edff927a3fd673eca904f8d8057d5ff6",
    "api/__init__.py": "a923e1a55ee842d8244dfbdfe4cd8b3e27eec61c5623abc95845abefda40727a",
    "api/administration.py": "4dddb6cad93c96bac42ec81331fac995424bb44adc42dccdace9d133ffb3e6b5",
    "api/contexts.py": "b59139523e33a0061388cd22ac5eb036af1f3faff03d28b2e9f07f120fad9e5f",
    "api/display.py": "4fe9438bf3016ca4ac6b97a17c0e20157e77d0b8b66dd4c1ff3054e901d65c03",
    "api/manifests.py": "5fbf98922ce23d6d15786553492ce42d7a07fba4fd09927ab8d4096ae6ef633f",
    "api/results.py": "5d6a59587e35e52a2528ec98a461d7c2e45705fd8d60f093f801795e81402135",
    "api/schema.py": "46628cb51be0e4a44df3606af2cbe841120cacf576f193ce57bc1af4b8a3d626",
    "api/services.py": "94b726f13e6e238db01ff37564c5fcddd6ef18b9fae3d6ab17f2e812845517d8",
    "api/storage.py": "ab5fab071b1ddee018053860a46a75019d6d83077d6d7f6c1fa6c1c945ec1db8",
    "api/subscriptions.py": "dddf75abfea96f8a88eecd71d6d6bb7d5f3327a52cb08185035fa2f5e0e6347a",
    "api/validation.py": "b6e0b2e16b8fae127c4cb8bf31efd0fce83c8b5b7ccf58256b01379fbf998e6c",
    "api/version.py": "e6be0b307d3f75aa324a0b0f5b4d196d408e0d968619a04672fbb4c7c0c6a2a2",
    "py.typed": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
}

SDK_API_MODULES = {
    "yomihime_sdk",
    "yomihime_sdk.api",
    *(
        "yomihime_sdk.api."
        + name.removeprefix("api/").removesuffix(".py").replace("/", ".")
        for name in SDK_PACKAGE_MANIFEST
        if name.startswith("api/") and name != "api/__init__.py"
    ),
}


class SDKBootstrapError(RuntimeError):
    """The bundled SDK does not match the pinned, supported artifact."""


def _resolved(path: str | Path) -> Path:
    return Path(path).resolve(strict=True)


def _require_contained(path: Path, parent: Path, label: str) -> Path:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise SDKBootstrapError(f"{label} escapes the plugin root") from exc
    return path


def _validate_sdk_tree(plugin_root: Path) -> ModuleType:
    try:
        sdk_root = _resolved(plugin_root / "yomihime_sdk")
    except OSError as exc:
        raise SDKBootstrapError("Bundled SDK package is missing or unreadable") from exc
    _require_contained(sdk_root, plugin_root, "SDK package root")
    if not sdk_root.is_dir():
        raise SDKBootstrapError("Bundled SDK package root is not a directory")
    actual_files = {
        path.relative_to(sdk_root).as_posix()
        for path in sdk_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    if actual_files != SDK_PACKAGE_MANIFEST.keys():
        missing = sorted(SDK_PACKAGE_MANIFEST.keys() - actual_files)
        extra = sorted(actual_files - SDK_PACKAGE_MANIFEST.keys())
        raise SDKBootstrapError(
            f"SDK package file set mismatch (missing={missing}, extra={extra})"
        )

    for relative, expected_digest in SDK_PACKAGE_MANIFEST.items():
        path = sdk_root / relative
        try:
            source_path = _resolved(path)
        except OSError as exc:
            raise SDKBootstrapError(f"SDK package file is missing: {relative}") from exc
        _require_contained(source_path, sdk_root, f"SDK package file {relative}")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_digest:
            raise SDKBootstrapError(f"SDK package content mismatch: {relative}")

    loaded = {
        name
        for name in sys.modules
        if name == "yomihime_sdk" or name.startswith("yomihime_sdk.")
    }
    unexpected = loaded - SDK_API_MODULES
    if unexpected:
        raise SDKBootstrapError(
            f"Unexpected preloaded SDK modules: {sorted(unexpected)}"
        )
    if loaded and loaded != SDK_API_MODULES:
        raise SDKBootstrapError("A partial Yomihime SDK package is already loaded")

    sdk = importlib.import_module("yomihime_sdk")
    importlib.import_module("yomihime_sdk.api")
    loaded = {
        name
        for name in sys.modules
        if name == "yomihime_sdk" or name.startswith("yomihime_sdk.")
    }
    if loaded != SDK_API_MODULES:
        raise SDKBootstrapError(
            f"Loaded SDK module set differs from the pinned package: {sorted(loaded)}"
        )
    for name in SDK_API_MODULES:
        module = sys.modules.get(name)
        if module is None:
            raise SDKBootstrapError(f"SDK module is missing: {name}")
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None)
        if (
            getattr(module, "__name__", None) != name
            or getattr(spec, "name", None) != name
        ):
            raise SDKBootstrapError(f"SDK module identity mismatch: {name}")
        if not isinstance(
            getattr(spec, "loader", None), importlib.machinery.SourceFileLoader
        ):
            raise SDKBootstrapError(f"SDK module is not source-backed: {name}")
        if name in {"yomihime_sdk", "yomihime_sdk.api"}:
            expected_path = sdk_root / (
                "__init__.py" if name == "yomihime_sdk" else "api/__init__.py"
            )
            locations = getattr(module, "__path__", ())
            expected_location = expected_path.parent
            if len(locations) != 1 or _resolved(locations[0]) != expected_location:
                raise SDKBootstrapError(f"SDK package search path mismatch: {name}")
        else:
            relative = name.removeprefix("yomihime_sdk.").replace(".", "/") + ".py"
            expected_path = sdk_root / relative
        if origin is None:
            raise SDKBootstrapError(f"SDK module origin is missing: {name}")
        origin_path = _resolved(origin)
        _require_contained(origin_path, plugin_root, f"SDK module origin {name}")
        if origin_path != expected_path:
            raise SDKBootstrapError(f"SDK module origin mismatch: {name}")
        source = getattr(module, "__file__", None)
        if source is None:
            raise SDKBootstrapError(f"SDK module source is missing: {name}")
        source_path = _resolved(source)
        _require_contained(source_path, plugin_root, f"SDK module source {name}")
        if source_path != expected_path:
            raise SDKBootstrapError(f"SDK module source mismatch: {name}")

    version = sys.modules["yomihime_sdk.api.version"]
    if (
        sdk.__version__ != SDK_VERSION
        or version.CONTRACT_VERSION != SDK_VERSION
        or version.CONTRACT_REVISION != SDK_CONTRACT_REVISION
        or tuple(version.COMPATIBLE_CONTRACT_VERSIONS)
        != SDK_COMPATIBLE_CONTRACT_VERSIONS
    ):
        raise SDKBootstrapError("Unsupported Yomihime SDK contract version")
    return sdk


def bootstrap_sdk(plugin_root: str | Path | None = None) -> ModuleType:
    """Load only the exact pinned SDK bundled at this plugin's resolved root."""
    root = _resolved(plugin_root or Path(__file__).resolve().parent)
    inserted_entry: str | None = None
    root_text = str(root)
    if not any(
        Path(entry).resolve() == root
        for entry in sys.path
        if isinstance(entry, str) and entry
    ):
        inserted_entry = root_text
        sys.path.insert(0, inserted_entry)
    try:
        return _validate_sdk_tree(root)
    finally:
        if inserted_entry is not None:
            for index, entry in enumerate(sys.path):
                if entry is inserted_entry:
                    del sys.path[index]
                    break


bootstrap_sdk()

from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.star import Star, register  # noqa: E402


@register(
    "astrbot_plugin_yomihime_game_link",
    "yomihime",
    "如月怜的游戏连结：聚合游戏角色、战绩与资讯。",
    "0.1.2",
)
class YomihimeGameLink(Star):
    """Provide the entry point for the game information plugin."""

    def __init__(self, context, config: dict | None = None) -> None:
        super().__init__(context, config)
        from astrbot.api.star import StarTools

        from .adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime

        self._runtime = AstrBotRuntime(
            context,
            plugin_root=Path(__file__).resolve().parent,
            data_dir=StarTools.get_data_dir(PLUGIN_NAME),
        )

    async def initialize(self) -> None:
        await super().initialize()
        await self._runtime.initialize()

    async def terminate(self) -> None:
        await self._runtime.terminate()
        await super().terminate()

    @filter.command("ygl")
    async def game_link(self, event: AstrMessageEvent):
        """Dispatch generic module commands through the shared Core runtime.

        Args:
            event: The incoming command event.
        """
        # True disables AstrBot's default LLM path for this matched command.
        event.should_call_llm(True)
        text = await self._runtime.handle_event(event)
        if text is not None:
            yield event.plain_result(text)
