"""Synthetic declarations through the real catalog and fixed-page projection.

Lifecycle metadata is constructed offline; no Host, external Python or network runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ygl_test_subject.core.contracts.administration import ModuleLifecycle
from ygl_test_subject.core.lifecycle import LifecycleState
from ygl_test_subject.core.ports import RunIdentity
from ygl_test_subject.core.registry import RegisteredModule, RegistrySnapshot
from ygl_test_subject.extensions.static_page_projection import StaticPageProjection
from ygl_test_subject.services.module_catalog import project_module_catalog

from yomihime_game_link_sdk.services import ModuleHandlers

ROOT = Path(__file__).resolve().parents[2]
OWNER = "minimal/demo"
LOGO_PATH = "module-assets/minimal/demo/assets/logo.svg"
LOGO_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'


def external_catalog_fixture(*, with_logo=True):
    manifest = json.loads(
        (ROOT / "examples/modules/minimal/yomihime.manifest.json").read_bytes()
    )
    module = manifest["modules"][0]
    module["display"] = {"default_name": "Synthetic external module"}
    if with_logo:
        module["display"]["logo_resource"] = "assets/logo.svg"
        module["resources"].append(
            {
                "path": "assets/logo.svg",
                "sha256": hashlib.sha256(LOGO_BYTES).hexdigest(),
            }
        )
    raw = json.dumps(manifest).encode("utf-8")
    projection = StaticPageProjection((raw,))
    digest, parsed, _ = projection.packages["minimal"]
    module = parsed.modules[0]
    identity = RunIdentity("synthetic-runtime", OWNER, 1)
    snapshot = RegistrySnapshot(
        1,
        {OWNER: RegisteredModule(OWNER, module, ModuleHandlers({}, {}, {}), True, 1)},
        {module.route: OWNER},
        {},
    )
    state = LifecycleState(
        OWNER, True, ModuleLifecycle.ACTIVE, 1, snapshot.revision, identity=identity
    )
    catalog = project_module_catalog(
        snapshot, {OWNER: state}, {OWNER: identity}, runtime_state="ready"
    )
    return projection, catalog, digest


def export_catalog_cases():
    cases = {}
    for with_logo in (True, False):
        projection, catalog, digest = external_catalog_fixture(with_logo=with_logo)
        prefix = "logo" if with_logo else "no-logo"
        for branch, binding in (
            ("matched", digest),
            ("mismatch", "0" * 64),
            ("withdrawn", None),
        ):
            cases[f"{prefix}-{branch}"] = projection.catalog(
                catalog, {"minimal": binding}
            )
        if with_logo:
            tampered = copy.deepcopy(catalog)
            tampered["modules"][0]["resources"][0]["sha256"] = "0" * 64
            cases["logo-resources-hidden"] = projection.catalog(
                tampered, {"minimal": digest}
            )
            cases["logo-projection-missing"] = StaticPageProjection().catalog(
                catalog, {"minimal": digest}
            )
    return cases


if __name__ == "__main__":
    print(json.dumps(export_catalog_cases()))
