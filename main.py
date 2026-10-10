"""AstrBot entry point with a content-pinned, plugin-local SDK bootstrap."""

from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import sys
from pathlib import Path
from types import ModuleType

SDK_VERSION = "0.1.0a6"
SDK_MODULE_ABI_VERSION = "2.0"


SDK_PACKAGE_MANIFEST = {
    "__init__.py": "ceeebeea05dd8476ac530518686349fe41aba401cb7f2a27242ecbc8f87309e9",
    "contexts.py": "d9b44695677c47a0133dade501a0ca1cfc9847593062f5e5ff0337372bc34723",
    "declarations.py": "5496c80ead2ad530830fa7f57bc0600b609bae22990293a43ea43c1d68cb40ae",
    "display.py": "a43b38a8ad8bbe4e1a1c47be8aa8d9381ee28a349debe6365ebbcaed15acb51d",
    "errors.py": "bc1c83fe00f00251481fa22b76b77f50c886bb9b742e8bf32801d59e48e84e87",
    "py.typed": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "results.py": "0265846e191f0bcffa1e1948cd2832919de9566b88829d74862ce81cf4267f71",
    "services.py": "567a9342af5c5e52c90dc7ac8c9727d08f4f22775b255f400adc2e9e5be54c5a",
    "storage.py": "6311b4754da6e37616debd68003d6993cf89c0b060845387288e2de575313260",
    "subscriptions.py": "7513701bfabcef306bef2eed593163c357346da92a0ad9daa3ef56c2e7779d91",
    "version.py": "f242edbff8d178ed46d8e510b8cfec032347ae58a448fa38c670e53438600dd2",
    "_examples/empty_module/README.md": "c1150018aa2eb2bfb9f9129ba753f6f26d132a0a245d245eb8a62f3c6debc1d7",
    "_examples/empty_module/manifest.json": "9de2d0e3cbb7e32acf029677e59381d1bb8893c6cae84a45f9a606b6e3a7e965",
    "_examples/empty_module/module.py": "21fdb6428bd422804ad00e98038b032c25b1d2488b3df5f17e3be855e0c8198f",
    "_examples/offline_sample/README.md": "948ab435100048e6e2503c413a7e3e4387514729ffd62ec4c1cd53a9c504d5b2",
    "_examples/offline_sample/manifest.json": "2f0583a18c43501625ca47d94af7f9173c1b706413ae810b768edf309518c34d",
    "_examples/offline_sample/module.py": "6a3971d633d947671fdbcb9b6b0025a871ab21e615c0d0f57edc2b0e2be30f32",
}

SDK_MODULES = {
    "yomihime_game_link_sdk.results",
    "yomihime_game_link_sdk.storage",
    "yomihime_game_link_sdk.errors",
    "yomihime_game_link_sdk.services",
    "yomihime_game_link_sdk.contexts",
    "yomihime_game_link_sdk.subscriptions",
    "yomihime_game_link_sdk.display",
    "yomihime_game_link_sdk",
    "yomihime_game_link_sdk.version",
    "yomihime_game_link_sdk.declarations",
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
        sdk_root = _resolved(plugin_root / "yomihime_game_link_sdk")
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

    if any(
        name == "yomihime_sdk" or name.startswith("yomihime_sdk.")
        for name in sys.modules
    ):
        raise SDKBootstrapError("A retired Yomihime SDK type graph is already loaded")

    loaded = {
        name
        for name in sys.modules
        if name == "yomihime_game_link_sdk"
        or name.startswith("yomihime_game_link_sdk.")
    }
    unexpected = loaded - SDK_MODULES
    if unexpected:
        raise SDKBootstrapError(
            f"Unexpected preloaded SDK modules: {sorted(unexpected)}"
        )
    if loaded and loaded != SDK_MODULES:
        raise SDKBootstrapError("A partial Yomihime SDK package is already loaded")

    sdk = importlib.import_module("yomihime_game_link_sdk")
    loaded = {
        name
        for name in sys.modules
        if name == "yomihime_game_link_sdk"
        or name.startswith("yomihime_game_link_sdk.")
    }
    if loaded != SDK_MODULES:
        raise SDKBootstrapError(
            f"Loaded SDK module set differs from the pinned package: {sorted(loaded)}"
        )
    for name in SDK_MODULES:
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
        if name == "yomihime_game_link_sdk":
            expected_path = sdk_root / ("__init__.py")
            locations = getattr(module, "__path__", ())
            expected_location = expected_path.parent
            if len(locations) != 1 or _resolved(locations[0]) != expected_location:
                raise SDKBootstrapError(f"SDK package search path mismatch: {name}")
        else:
            relative = (
                name.removeprefix("yomihime_game_link_sdk.").replace(".", "/") + ".py"
            )
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

    version = sys.modules["yomihime_game_link_sdk.version"]
    if (
        sdk.__version__ != SDK_VERSION
        or version.MODULE_ABI_VERSION != SDK_MODULE_ABI_VERSION
    ):
        raise SDKBootstrapError("Unsupported Game Link SDK release or module ABI")
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

        from .adapters.astrbot.admin_pages import AdminPages
        from .adapters.astrbot.public_pages import PublicPages
        from .adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime

        self._runtime = AstrBotRuntime(
            context,
            plugin_root=Path(__file__).resolve().parent,
            data_dir=StarTools.get_data_dir(PLUGIN_NAME),
            config=config,
        )
        self._pages = PublicPages(context, self._runtime)
        self._admin_pages = AdminPages(context, self._runtime)

    async def initialize(self) -> None:
        await super().initialize()
        self._pages.register()
        self._admin_pages.register()
        try:
            await self._runtime.initialize()
        except BaseException:
            self._pages.close()
            self._admin_pages.close()
            raise

    async def terminate(self) -> None:
        self._pages.close()
        self._admin_pages.close()
        try:
            await self._runtime.terminate()
        finally:
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
