"""Rebuild generated projections and reject nondeterministic output bytes."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    "pages/shell",
    "pages/management",
    "pages/00-game-link",
    "modules/ff14/pages/dist",
)


def snapshot():
    files = [
        ROOT / "modules/ff14/pages/resources.json",
        ROOT / "modules/ff14/yomihime.manifest.json",
    ]
    for name in GENERATED:
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
