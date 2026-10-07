"""Offline explicit selection of the reviewed release, never production trust."""

from pathlib import Path

from ygl_test_subject.adapters.astrbot.bundled import _read_bundled_package
from ygl_test_subject.modules.ff14.assembly import SUPPORT
from ygl_test_subject.services.trusted_assembly import (
    CapturedAssemblyInventory,
    assemble_reviewed,
)

ROOT = Path(__file__).resolve().parents[2]


def selected_assembly(plugin_root=ROOT, principal_id="host"):
    if plugin_root == ROOT:
        # A bounded, explicitly selected synthetic fixture. The checkout's
        # parent modules/__pycache__ is not a production installation root.
        source = ROOT / "modules/ff14"
        files = {
            p.relative_to(source).as_posix(): p.read_bytes()
            for p in source.rglob("*.py")
        }
        for name in (
            "assembly.json",
            "pages/dist/entry.js",
            "pages/dist/styles.css",
            "pages/legacy/app.js",
            "pages/legacy/index.html",
            "pages/legacy/styles.css",
        ):
            files[name] = (source / name).read_bytes()
        payload = {
            "manifest": (source / "yomihime.manifest.json").read_bytes(),
            "sources": files,
        }
    else:
        payload = _read_bundled_package(plugin_root / "modules")
    return assemble_reviewed(
        CapturedAssemblyInventory("ff14", payload["manifest"], payload["sources"]),
        principal_id=principal_id,
        support=SUPPORT,
        selected=True,
    )


def ordinary_migration_fields(principal_id):
    return selected_assembly(principal_id=principal_id).migration_fields
