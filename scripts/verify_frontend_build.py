"""Rebuild generated projections and reject nondeterministic output bytes."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ("pages/00-game-link", ".build/frontend")


def snapshot():
    files = []
    directories = list(GENERATED)
    for path in sorted((ROOT / "modules").glob("*/yomihime.manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        files.extend((path, path.parent / "pages/resources.json"))
        if any(
            page["entry"].startswith("pages/dist/")
            for module in manifest["modules"]
            for page in module.get("pages", ())
        ):
            directories.append(
                (path.parent / "pages/dist").relative_to(ROOT).as_posix()
            )
    for name in directories:
        directory = ROOT / name
        if not directory.is_dir():
            raise ValueError("frontend must be built before verification")
        files.extend(p for p in directory.rglob("*") if p.is_file())
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
    }


def main():
    before = snapshot()
    subprocess.run(["node", "pages/frontend/build.mjs"], cwd=ROOT, check=True)
    after = snapshot()
    if before != after:
        raise ValueError("frontend rebuild changed generated projections")
    print(f"Verified {len(after)} reproducible frontend files")


if __name__ == "__main__":
    main()
