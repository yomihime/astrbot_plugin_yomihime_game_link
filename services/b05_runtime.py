"""Bounded B05 offline artifact preflight helpers.

This module deliberately stops at installed SDK resources and E's static,
disabled discovery boundary. It does not import extension modules or activate
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

_EXAMPLES = ("empty_module", "offline_sample")
_RESOURCE_FILES = ("manifest.json", "module.py", "README.md")


@dataclass(frozen=True, slots=True)
class ExtractedSdkExample:
    """One example extracted from the installed SDK resource package."""

    package_id: str
    directory: Path
    manifest_path: Path


def extract_installed_sdk_examples(
    extension_root: Path,
) -> tuple[ExtractedSdkExample, ...]:
    """Extract both packaged examples into a fresh, caller-owned directory.

    The resource package is imported, but its ``module.py`` is only copied as
    bytes. E remains responsible for static manifest discovery; this helper
    never imports a factory or performs registration.
    """

    root = Path(extension_root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("extension root must be an existing ordinary directory")
    if any(root.iterdir()):
        raise ValueError("extension root must be empty")

    extracted: list[ExtractedSdkExample] = []
    for package_id in _EXAMPLES:
        package = resources.files(f"yomihime_game_link_sdk._examples.{package_id}")
        destination = root / package_id
        destination.mkdir()
        for filename in _RESOURCE_FILES:
            target_name = (
                "yomihime.manifest.json" if filename == "manifest.json" else filename
            )
            destination.joinpath(target_name).write_bytes(
                package.joinpath(filename).read_bytes()
            )
        extracted.append(
            ExtractedSdkExample(
                package_id=package_id,
                directory=destination,
                manifest_path=destination / "yomihime.manifest.json",
            )
        )
    return tuple(extracted)


__all__ = ["ExtractedSdkExample", "extract_installed_sdk_examples"]
