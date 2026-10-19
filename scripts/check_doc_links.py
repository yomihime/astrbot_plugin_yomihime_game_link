"""Check repository-relative Markdown links without network access."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {".coordination", ".architecture-refactor"}


def check(root=ROOT):
    errors = []
    for path in [*root.glob("*.md"), *(root / "docs").rglob("*.md")]:
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"```.*?```", "", source, flags=re.S)
        for match in re.finditer(r"\[[^\]\n]*\]\(([^)\n]+)\)", source):
            url = urlsplit(match[1])
            if url.scheme or url.netloc or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            try:
                relative = target.relative_to(root)
            except ValueError:
                errors.append(f"{path.relative_to(root)}: {match[1]}")
                continue
            if PRIVATE.intersection(relative.parts) or not target.exists():
                errors.append(f"{path.relative_to(root)}: {match[1]}")
    return errors


if __name__ == "__main__":
    failures = check()
    if failures:
        raise SystemExit("\n".join(failures))
    print("Repository-relative documentation links verified")
