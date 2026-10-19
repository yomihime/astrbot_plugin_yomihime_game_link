"""Fetch the exact OFL font fixture from its pinned official source.

Development/test preparation only. Runtime font acquisition belongs to the
managed font service; this script never accesses user data directories.
"""

from __future__ import annotations

import ast
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _constant(path: Path, name: str):
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("missing pinned fixture constant")


def prepare(root: Path = ROOT) -> Path:
    provider = root / "presentation/bundled_font.py"
    size = _constant(provider, "FONT_SIZE")
    digest = _constant(provider, "FONT_SHA256")
    destination = (
        root / "presentation/assets/fonts" / _constant(provider, "FONT_FILENAME")
    )

    def valid(data):
        return len(data) == size and hashlib.sha256(data).hexdigest() == digest

    if destination.exists():
        if valid(destination.read_bytes()):
            return destination
        raise ValueError(
            "existing test font failed integrity verification; not replaced"
        )
    url = _constant(root / "services/managed_font.py", "FONT_URL")
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(size + 1)
    if not valid(data):
        raise ValueError("downloaded test font failed integrity verification")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    print(prepare())
