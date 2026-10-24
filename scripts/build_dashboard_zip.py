"""Build a Dashboard ZIP containing only the independently pinned SDK wheel."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import stat
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "dist" / "astrbot_plugin_yomihime_game_link-local.zip"
EXPECTED_WHEEL_SHA256 = (
    "9f429148ac8c469b9e92bf9b0097bf27c898afed3d20c933519d1851e991e481"
)
EXPECTED_WHEEL_ENTRIES = {
    "yomihime_game_link_sdk/host.py",
    "yomihime_game_link_sdk/cards.py",
    "yomihime_game_link_sdk/_examples/offline_sample/manifest.json",
    "yomihime_game_link_sdk/_examples/offline_sample/module.py",
    "yomihime_game_link_sdk/errors.py",
    "yomihime_game_link_sdk/display.py",
    "yomihime_game_link_sdk/services.py",
    "yomihime_game_link_sdk/declarations.py",
    "yomihime_game_link_sdk/_examples/offline_sample/README.md",
    "yomihime_game_link_sdk/version.py",
    "yomihime_game_link_sdk/results.py",
    "yomihime_game_link_sdk/_examples/empty_module/README.md",
    "yomihime_game_link_sdk-0.1.0a16.dist-info/licenses/SDK-LICENSE",
    "yomihime_game_link_sdk-0.1.0a16.dist-info/METADATA",
    "yomihime_game_link_sdk/_examples/empty_module/module.py",
    "yomihime_game_link_sdk/storage.py",
    "yomihime_game_link_sdk-0.1.0a16.dist-info/top_level.txt",
    "yomihime_game_link_sdk/subscriptions.py",
    "yomihime_game_link_sdk/contexts.py",
    "yomihime_game_link_sdk-0.1.0a16.dist-info/WHEEL",
    "yomihime_game_link_sdk/py.typed",
    "yomihime_game_link_sdk/__init__.py",
    "yomihime_game_link_sdk-0.1.0a16.dist-info/RECORD",
    "yomihime_game_link_sdk/_examples/empty_module/manifest.json",
}
PACKAGE_ENTRY_NAMES = {
    name
    for name in EXPECTED_WHEEL_ENTRIES
    if name.startswith("yomihime_game_link_sdk/")
}
ROOT_FILES = (
    "main.py",
    "_conf_schema.json",
    "__init__.py",
    "metadata.yaml",
    "logo.png",
    "LICENSE",
    "SDK-LICENSE",
    "README.md",
    "CHANGELOG.md",
    "requirements.txt",
)
HOST_METADATA_FILES = (
    ".astrbot-plugin/i18n/zh-CN.json",
    ".astrbot-plugin/i18n/en-US.json",
)
OPERATOR_SCRIPT_FILES = (
    "scripts/__init__.py",
    "scripts/admin_credentials.py",
    "scripts/configure_source_credentials.py",
    "scripts/migrate_private_storage.py",
)
MAINTENANCE_HELPER_FILES = ("infrastructure/windows/maintenance.py",)
SHELL_FILES = (
    "index.html",
    "app.js",
    "runtime.js",
    "styles.css",
    "THIRD_PARTY_NOTICES.txt",
    "licenses/@css-render_plugin-bem.txt",
    "licenses/@css-render_vue3-ssr.txt",
    "licenses/@emotion_hash.txt",
    "licenses/@juggle_resize-observer.txt",
    "licenses/@vue_reactivity.txt",
    "licenses/@vue_runtime-core.txt",
    "licenses/@vue_runtime-dom.txt",
    "licenses/@vue_shared.txt",
    "licenses/css-render.txt",
    "licenses/date-fns.txt",
    "licenses/evtd.txt",
    "licenses/lodash-es.txt",
    "licenses/naive-ui.txt",
    "licenses/seemly.txt",
    "licenses/treemate.txt",
    "licenses/vdirs.txt",
    "licenses/vooks.txt",
    "licenses/vue.txt",
    "licenses/vueuc.txt",
)
PAGE_FILES = tuple("pages/00-game-link/" + name for name in SHELL_FILES)
FF14_BUNDLE_ROOT = Path("modules") / "ff14"
# Trusted release composition metadata, reviewed independently of candidate
# source trees and manifests. New source files require an explicit review.
REVIEWED_BUNDLE_SOURCES = {
    ("modules", "ff14"): frozenset(
        {
            "__init__.py",
            "assembly.py",
            "config.py",
            "features/__init__.py",
            "features/calendar.py",
            "features/calendar_diagnostics.py",
            "features/calendar_evaluator.py",
            "features/calendar_ics.py",
            "features/calendar_models.py",
            "features/calendar_snapshots.py",
            "features/calendar_subscriptions.py",
            "features/chat_context.py",
            "features/diagnostic_declarations.py",
            "features/duty_order.py",
            "features/fflogs.py",
            "features/fflogs_cards.py",
            "features/fflogs_catalog.py",
            "features/fflogs_chat.py",
            "features/fflogs_evidence.py",
            "features/fflogs_history_cache.py",
            "features/fflogs_jobs.py",
            "features/fflogs_models.py",
            "features/fflogs_scope_contract.py",
            "features/fflogs_server.py",
            "features/item_cards.py",
            "features/item_chat.py",
            "features/item_resolution.py",
            "features/item_sources.py",
            "features/items.py",
            "features/market.py",
            "features/market_cards.py",
            "features/market_handler.py",
            "features/market_models.py",
            "features/market_scope_chat.py",
            "features/market_sources.py",
            "features/public_projection.py",
            "features/status_cards.py",
            "models.py",
            "module.py",
            "presentation.py",
            "query_resolution.py",
        }
    ),
    ("examples/modules", "minimal"): frozenset(
        {
            "assembly.py",
            "module.py",
        }
    ),
}
FF14_BUNDLE_REQUIRED_FILES = set(REVIEWED_BUNDLE_SOURCES[("modules", "ff14")]) | {
    "assembly.json",
    "yomihime.manifest.json",
    "README.md",
}
RUNTIME_DIRS = (
    "adapters",
    "core",
    "extensions",
    "services",
    "infrastructure",
    "presentation",
)
ALLOWED_SUFFIXES = {".py", ".sql"}
# Exact reviewed binary/legal inputs. Never broaden the runtime suffix set or
# recursively admit presentation/assets (including Python files placed there).
FONT_ASSETS = {
    "presentation/assets/fonts/OFL.txt": (
        4301,
        "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2",
    ),
    "presentation/assets/fonts/NOTICE.md": (
        3131,
        "9c4def9efd3007da62abc3f4be270e644b9afc66f749c20b6ed091147fec615b",
    ),
}
EXCLUDED_RUNTIME_DIRS = {
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "cache",
    "caches",
    "data",
    "instance",
    "local-data",
    "local_data",
}


def validate_public_paths(paths) -> None:
    for path in paths:
        normalized = str(path).replace("\\", "/").casefold()
        if ".local-dev" in normalized.split("/") or normalized == "agents.md":
            raise ValueError(f"Local development material is not public input: {path}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(path: Path, root: Path, label: str) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its allowed root") from exc
    return path


def _reject_link(path: Path, metadata: os.stat_result, label: str) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    is_junction = getattr(path, "is_junction", lambda: False)()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or is_junction
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
    ):
        raise ValueError(
            f"{label} must not be a symlink, junction, or reparse point: {path}"
        )
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
        raise ValueError(f"{label} must not be a hard link: {path}")


def _path_parts(path: Path) -> list[Path]:
    parts = [path]
    parts.extend(path.parents)
    return list(reversed(parts))


def validate_output_paths(paths, inputs=()) -> tuple[Path, ...]:
    """Preflight every destination before any artifact is published."""
    outputs = tuple(Path(os.path.abspath(path)) for path in paths)
    protected = {Path(os.path.abspath(path)) for path in inputs}
    if len(outputs) != len(set(outputs)):
        raise ValueError("Build outputs must be distinct")
    for path in outputs:
        if path in protected:
            raise ValueError("Build output overlaps an input")
        for component in _path_parts(path):
            try:
                metadata = component.lstat()
            except FileNotFoundError:
                continue
            _reject_link(component, metadata, "Build output")
            if component != path and not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("Build output parent must be a directory")
            if component == path and not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Build output must be a regular file")
    return outputs


def publish_artifacts(payloads: dict[Path, bytes], inputs=()) -> None:
    """Stage bytes beside their destinations; never truncate an existing file."""
    outputs = validate_output_paths(payloads, inputs)
    staged = []
    try:
        for destination, content in zip(outputs, payloads.values(), strict=True):
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
            )
            temporary = Path(temporary)
            staged.append((temporary, destination))
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
        validate_output_paths(outputs, inputs)
        for temporary, destination in staged:
            temporary.replace(destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


@contextmanager
def _checkout_imports():
    """Load trusted checkout code independently of its directory's spelling."""
    prefix = "_ygl_release_build"
    namespaces = (prefix, "yomihime_game_link_sdk")

    def owned(name):
        return any(name == base or name.startswith(base + ".") for base in namespaces)

    previous = {name: module for name, module in sys.modules.items() if owned(name)}
    for name in previous:
        del sys.modules[name]
    try:
        for name, directory in (
            (prefix, ROOT),
            ("yomihime_game_link_sdk", ROOT / "yomihime_game_link_sdk"),
        ):
            spec = spec_from_file_location(
                name,
                directory / "__init__.py",
                submodule_search_locations=[str(directory)],
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Cannot load trusted build validators")
            module = module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        yield prefix
    finally:
        for name in tuple(sys.modules):
            if owned(name):
                del sys.modules[name]
        sys.modules.update(previous)


def validate_source_path(
    path: Path, allowed_root: Path, label: str = "Package input"
) -> Path:
    """Resolve a package input only after rejecting links and root escapes."""
    root = Path(allowed_root).resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    _inside(candidate, root, label)
    relative = candidate.relative_to(root)
    current = root
    root_metadata = current.lstat()
    _reject_link(current, root_metadata, label)
    for part in relative.parts:
        current = current / part
        metadata = current.lstat()
        _reject_link(current, metadata, label)
    resolved = candidate.resolve(strict=True)
    _inside(resolved, root, label)
    return resolved


def _validate_artifact_path(path: Path) -> Path:
    """Reject linked/reparse components for the explicitly supplied wheel."""
    candidate = Path(os.path.abspath(path))
    for component in _path_parts(candidate):
        metadata = component.lstat()
        _reject_link(component, metadata, "SDK wheel input")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("SDK wheel input must be a regular file")
    return resolved


def _file_token(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _read_snapshot(
    path: Path,
    allowed_root: Path | None = None,
    label: str = "Package input",
    max_bytes: int | None = None,
) -> bytes:
    """Read one opened file object after checking its path and handle identity."""
    candidate = Path(path)
    safe_path = (
        validate_source_path(candidate, allowed_root, label)
        if allowed_root is not None
        else _validate_artifact_path(candidate)
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(safe_path, flags)
    try:
        opened_before = os.fstat(descriptor)
        _reject_link(safe_path, opened_before, label)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError(f"{label} must be a regular file: {safe_path}")

        current_path = (
            validate_source_path(candidate, allowed_root, label)
            if allowed_root is not None
            else _validate_artifact_path(candidate)
        )
        current_before = current_path.stat()
        if _file_token(opened_before) != _file_token(current_before):
            raise ValueError(f"{label} changed while it was being opened: {candidate}")

        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read() if max_bytes is None else stream.read(max_bytes + 1)

        opened_after = os.fstat(descriptor)
        current_path = (
            validate_source_path(candidate, allowed_root, label)
            if allowed_root is not None
            else _validate_artifact_path(candidate)
        )
        current_after = current_path.stat()
        if _file_token(opened_before) != _file_token(opened_after) or _file_token(
            opened_after
        ) != _file_token(current_after):
            raise ValueError(
                f"{label} changed while its snapshot was read: {candidate}"
            )
        return content
    finally:
        os.close(descriptor)


def validate_wheel(path: Path) -> dict[str, bytes]:
    wheel_path = _validate_artifact_path(path)
    wheel_bytes = _read_snapshot(wheel_path, label="SDK wheel input")
    if _sha256(wheel_bytes) != EXPECTED_WHEEL_SHA256:
        raise ValueError("SDK wheel does not match the independently pinned SHA-256")
    with zipfile.ZipFile(BytesIO(wheel_bytes)) as wheel:
        names = wheel.namelist()
        if len(names) != len(set(names)) or set(names) != EXPECTED_WHEEL_ENTRIES:
            raise ValueError(
                "SDK wheel entry manifest differs from the reviewed artifact"
            )
        metadata_lines = set(
            wheel.read("yomihime_game_link_sdk-0.1.0a16.dist-info/METADATA")
            .decode("utf-8")
            .splitlines()
        )
        if not {"Name: yomihime-game-link-sdk", "Version: 0.1.0a16"} <= metadata_lines:
            raise ValueError("SDK wheel distribution metadata is unsupported")
        package = {name: wheel.read(name) for name in sorted(PACKAGE_ENTRY_NAMES)}
    return package


def _runtime_files(directory: Path, repository_root: Path) -> list[Path]:
    found: list[Path] = []
    pending = [directory]
    while pending:
        current = pending.pop()
        for entry in sorted(current.iterdir(), key=lambda item: item.name):
            resolved = validate_source_path(entry, repository_root, "Runtime source")
            metadata = resolved.stat()
            if stat.S_ISDIR(metadata.st_mode):
                if entry == repository_root / "presentation/assets":
                    continue
                if entry.name not in EXCLUDED_RUNTIME_DIRS:
                    pending.append(resolved)
            elif stat.S_ISREG(metadata.st_mode):
                if resolved.suffix in ALLOWED_SUFFIXES:
                    found.append(resolved)
            else:
                raise ValueError(
                    f"Runtime source is not a regular file or directory: {entry}"
                )
    return found


def _font_asset_snapshot(path: Path, repository_root: Path) -> bytes:
    name = path.relative_to(repository_root).as_posix()
    size, digest = FONT_ASSETS[name]
    content = _read_snapshot(
        path, repository_root, "Bundled font asset", max_bytes=size
    )
    if len(content) != size or _sha256(content) != digest:
        raise ValueError("Bundled font asset differs from its reviewed size/hash")
    return content


def _reviewed_bundle_payloads(repository_root: Path, *, bundle_roots=("modules",)):
    """Read trusted build-selected source roots; manifests never choose imports."""
    with _checkout_imports() as namespace:
        adapter = import_module(f"{namespace}.adapters.astrbot.bundled")
        return _capture_reviewed_bundles(adapter, repository_root, bundle_roots)


def _capture_reviewed_bundles(adapter, repository_root, bundle_roots):
    payloads = []
    identities = set()
    for relative in bundle_roots:
        # Use the same typed trusted selection path validation as runtime.
        adapter.ReviewedBundleSelection("validation", relative)
        source_root = validate_source_path(
            repository_root / relative, repository_root, "Reviewed bundle root"
        )
        for child in sorted(source_root.iterdir(), key=lambda item: item.name):
            checked = validate_source_path(child, repository_root, "Reviewed package")
            if (
                not checked.is_dir()
                or not (checked / "yomihime.manifest.json").exists()
            ):
                continue
            selection = adapter.ReviewedBundleSelection(child.name, relative)
            expected_sources = REVIEWED_BUNDLE_SOURCES.get(
                (selection.source_directory, selection.package_id)
            )
            if expected_sources is None:
                raise ValueError("Unsupported reviewed release composition")
            payload = adapter._read_bundled_package(source_root, selection)
            actual_sources = {
                name for name in payload["sources"] if name.endswith(".py")
            }
            if actual_sources != expected_sources:
                raise ValueError("Unsupported reviewed bundle source inventory")
            if child.name in identities:
                raise ValueError("Duplicate reviewed package identity")
            identities.add(child.name)
            payloads.append((checked, payload))
    return payloads


def _reviewed_bundle_files(repository_root: Path) -> list[Path]:
    found = []
    for package_root, payload in _reviewed_bundle_payloads(repository_root):
        for relative in (*payload["sources"], "yomihime.manifest.json", "README.md"):
            found.append(
                validate_source_path(
                    package_root / relative, repository_root, "Reviewed bundle input"
                )
            )
    return found


def _bundle_readers(namespace):
    # The caller owns this namespace until every validator invocation completes,
    # including delayed relative imports and strict SDK DTO identity checks.
    parse_manifest = import_module(
        f"{namespace}.extensions.disk_manifest"
    ).parse_manifest
    read_page_resources = import_module(
        f"{namespace}.extensions.page_resources"
    ).read_page_resources
    return parse_manifest, read_page_resources


def projected_page_assets(
    repository_root: Path, *, bundle_roots=("modules",), external_page_roots=()
) -> dict[str, bytes]:
    """Project declared hash-pinned resources into the sole authenticated page root."""
    with _checkout_imports() as namespace:
        return _projected_page_assets(
            repository_root, bundle_roots, namespace, external_page_roots
        )


def _projected_page_assets(
    repository_root, bundle_roots, namespace, external_page_roots=()
):
    parse_manifest, read_page_resources = _bundle_readers(namespace)
    adapter = import_module(f"{namespace}.adapters.astrbot.bundled")
    payload = {}
    branches = []
    styles = set()
    prefix = "pages/00-game-link/"
    builtin_packages = set()
    for package_root, bundle in _capture_reviewed_bundles(
        adapter, repository_root, bundle_roots
    ):
        manifest = parse_manifest(bundle["manifest"])
        builtin_packages.add(manifest.package_id)
        resources = read_page_resources(package_root, manifest)
        for module in manifest.modules:
            owner = manifest.global_module_id(module.module_id)
            for resource in module.resources:
                payload[f"{prefix}module-assets/{owner}/{resource.path}"] = resources[
                    resource.path
                ]
            for page in module.pages:
                entry = f"module-assets/{owner}/{page.entry}"
                branches.append(
                    f"case {json.dumps(entry)}: return import({json.dumps('./' + entry)});"
                )
                styles.update(f"module-assets/{owner}/{style}" for style in page.styles)
    projection_module = import_module(f"{namespace}.extensions.static_page_projection")
    projection, external_resources = projection_module.capture_static_pages(
        external_page_roots
    )
    if builtin_packages.intersection(projection.packages):
        raise ValueError("external page input cannot replace a built-in package")
    for path, content in external_resources.items():
        name = prefix + path
        if name.casefold() in {key.casefold() for key in payload}:
            raise ValueError("external page input collides with static composition")
        payload[name] = content
    for digest, manifest, _ in projection.packages.values():
        for module in manifest.modules:
            owner = manifest.global_module_id(module.module_id)
            for page in module.pages:
                entry = projection_module.projected_path(owner, digest, page.entry)
                branches.append(
                    f"case {json.dumps(entry)}: return import({json.dumps('./' + entry)});"
                )
                styles.update(
                    projection_module.projected_path(owner, digest, path)
                    for path in page.styles
                )
    if projection.packages:
        payload[projection_module.PROJECTION_FILE] = projection.serialize()
    payload[prefix + "module-loader.js"] = (
        "export async function loadPage(entry) {\nswitch (entry) {\n"
        + "\n".join(sorted(set(branches)))
        + "\ndefault: throw new Error('Unknown module page');\n}\n}\n"
    ).encode()
    index = _read_snapshot(
        repository_root / (prefix + "index.html"), repository_root, "Page index"
    ).decode("utf-8")
    import re

    marker = re.compile(r'<template id="module-style-assets">.*?</template>', re.DOTALL)
    if len(marker.findall(index)) != 1 or 'data-page-name="00-game-link"' not in index:
        raise ValueError(
            "Page index must declare one inert module style template and exact page identity"
        )
    links = "".join(
        f'<link rel="stylesheet" data-resource="{html.escape(path, quote=True)}" href="./{html.escape(path, quote=True)}">'
        for path in sorted(styles)
    )
    payload[prefix + "index.html"] = marker.sub(
        lambda _: '<template id="module-style-assets">' + links + "</template>", index
    ).encode("utf-8")
    return payload


def included_files(root: Path = ROOT) -> list[Path]:
    repository_root = Path(root).resolve(strict=True)
    files = [
        validate_source_path(
            repository_root / name, repository_root, "Plugin root input"
        )
        for name in ROOT_FILES
    ]
    for name in PAGE_FILES:
        if not (repository_root / name).exists():
            raise FileNotFoundError(
                f"Missing frontend projection {name}; run npm ci --prefix pages/frontend "
                "then npm run build --prefix pages/frontend before packaging"
            )
    for path in sorted((repository_root / "modules").glob("*/yomihime.manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        generated = [
            resource["path"]
            for module in manifest.get("modules", ())
            for resource in module.get("resources", ())
            if resource["path"].startswith("pages/dist/")
        ]
        if generated:
            for name in ("pages/resources.json", *generated):
                if not (path.parent / name).exists():
                    raise FileNotFoundError(
                        f"Missing frontend projection {path.parent.name}/{name}; "
                        "run npm run build --prefix pages/frontend before packaging"
                    )
    files.extend(
        validate_source_path(repository_root / name, repository_root, "Host metadata")
        for name in HOST_METADATA_FILES
    )
    files.extend(
        validate_source_path(repository_root / name, repository_root, "Operator script")
        for name in OPERATOR_SCRIPT_FILES
    )
    files.extend(
        validate_source_path(repository_root / name, repository_root, "Page asset")
        for name in PAGE_FILES
    )
    files.extend(
        validate_source_path(
            repository_root / name, repository_root, "Maintenance helper"
        )
        for name in MAINTENANCE_HELPER_FILES
    )
    for directory in RUNTIME_DIRS:
        runtime_root = validate_source_path(
            repository_root / directory, repository_root, "Runtime directory"
        )
        files.extend(_runtime_files(runtime_root, repository_root))
    for name in FONT_ASSETS:
        path = validate_source_path(
            repository_root / name, repository_root, "Bundled font asset"
        )
        _font_asset_snapshot(path, repository_root)
        files.append(path)
    files.extend(_reviewed_bundle_files(repository_root))

    validate_public_paths(
        path.relative_to(repository_root).as_posix() for path in files
    )
    return sorted(
        set(files), key=lambda path: path.relative_to(repository_root).as_posix()
    )


def build(
    sdk_wheel: Path,
    archive_path: Path = ARCHIVE,
    repository_root: Path = ROOT,
    *,
    external_page_roots=(),
) -> Path:
    package = validate_wheel(sdk_wheel)
    repository_root = Path(repository_root).resolve(strict=True)
    if any(
        Path(archive_path).absolute().is_relative_to(Path(root).absolute())
        for root in external_page_roots
    ):
        raise ValueError("Build output overlaps an external page input")
    files = included_files(repository_root)
    page_assets = projected_page_assets(
        repository_root, external_page_roots=external_page_roots
    )
    inputs = (*files, sdk_wheel, *external_page_roots)
    validate_output_paths((archive_path,), inputs)
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(repository_root).as_posix()
            content = (
                _font_asset_snapshot(path, repository_root)
                if name in FONT_ASSETS
                else _read_snapshot(path, repository_root, "Runtime source")
            )
            if name in page_assets:
                content = page_assets[name]
            _write_entry(archive, name, content)
        for name, data in package.items():
            _write_entry(archive, name, data)
        for name, data in page_assets.items():
            if name not in {
                path.relative_to(repository_root).as_posix() for path in files
            }:
                _write_entry(archive, name, data)

    with zipfile.ZipFile(BytesIO(output.getvalue())) as result:
        names = result.namelist()
        expected_names = (
            {path.relative_to(repository_root).as_posix() for path in files}
            | set(package)
            | set(page_assets)
        )
        if len(names) != len(set(names)) or set(names) != expected_names:
            raise RuntimeError("Generated ZIP entry manifest is not exact")
        for name, data in package.items():
            if result.read(name) != data:
                raise RuntimeError(f"SDK bytes differ from the pinned wheel: {name}")
        for name, data in page_assets.items():
            if result.read(name) != data:
                raise RuntimeError(f"Module asset differs from verified bundle: {name}")
        if result.testzip() is not None:
            raise RuntimeError("The generated ZIP failed its integrity check")
    publish_artifacts({archive_path: output.getvalue()}, inputs)
    return archive_path


def _write_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    validate_public_paths((name,))
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sdk-wheel",
        required=True,
        type=Path,
        help="Path to the offline K-reviewed wheel; its digest is checked against the built-in pin.",
    )
    parser.add_argument(
        "--external-page-package",
        action="append",
        type=Path,
        default=[],
        help="Explicit reviewed package root; capture only hash-declared static pages, never Python.",
    )
    args = parser.parse_args()
    archive = build(args.sdk_wheel, external_page_roots=args.external_page_package)
    print(f"Created {archive}")
    print(f"Included {len(included_files()) + len(PACKAGE_ENTRY_NAMES)} files")


if __name__ == "__main__":
    main()
