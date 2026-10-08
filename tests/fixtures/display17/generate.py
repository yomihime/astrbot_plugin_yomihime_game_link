"""Regenerate synthetic goldens with the actual SDK/serializer at BASELINE.

Normal tests only read the checked-in JSON; regeneration needs Git history.
The old SDK runs in an isolated child and never imports the current package.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASELINE = "da16405f613a01de5b8844d7bdb6b96156c4a098"
MODULES = ("version", "display", "subscriptions", "storage", "contexts", "schema")
ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent

CHILD = r"""
import importlib, json, sys, types
from pathlib import Path
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
root = Path(sys.argv[1])
for name, path in (("legacy", root), ("legacy.api", root / "api")):
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
from legacy.api.display import *
from legacy.api.storage import GrantReference
from legacy.api.subscriptions import *
from serializer import _dump
now = datetime(2026, 9, 25, 12, tzinfo=UTC)
blocks = (
    TextBlock("legacy-body"),
    FieldsBlock({"nested": {"bool": True, "none": None, "decimal": Decimal("1.234"), "number": NumberValue("12.5", "unit", 2), "date": TimeValue(date(2026, 9, 25), "UTC")}}),
    MetricsBlock({"money": MoneyValue("9.25", "USD", 2)}),
    TableBlock(("a",), ((TimeValue(now, "UTC"),),)),
    ItemGridBlock((GridItem("item", 4, "img:synthetic"),)),
    ImageBlock("img:synthetic", "synthetic image", required=False, fallback_text="image fallback"),
    SeriesBlock(((TimeValue(now), NumberValue(3)),), required=False),
    LinksBlock((Link("synthetic", "https://example.invalid/fixture"),), required=False),
    CommandsBlock(("/synthetic",), required=False),
    UnknownBlock("optional-synthetic", required=False, fallback_text="unknown fallback"),
)
events = {}
for privacy in (Privacy.PUBLIC, Privacy.PRIVATE):
    recipient = ConversationRef("adapter", ConversationKind.DIRECT, "u1", "private-u1")
    grant = None if privacy is Privacy.PUBLIC else GrantReference("synthetic-grant", 2)
    for state in (DeliveryState.PENDING, DeliveryState.FAILED):
        name = privacy.value + "-" + state.value
        key = "legacy-" + name
        identity = delivery_idempotency_key(key, 1, "sub-1", 1, recipient)
        attempt = None if state is DeliveryState.PENDING else DeliveryAttempt(1, state, identity, now - timedelta(minutes=2), now - timedelta(minutes=1), "known_unsent")
        event = DeliveryEvent(key, 1, "sub-1", 1, "u1", grant, recipient,
            DisplayDocument("Legacy", "Synthetic", blocks, ("synthetic-source",), (TimeValue(now, "UTC"),), privacy),
            identity, state, attempt, None if attempt is None else now)
        events[name] = _dump(event)
print(json.dumps(events, ensure_ascii=False, sort_keys=True, indent=2))
"""


def main() -> None:
    provenance = {"baseline": BASELINE, "files": {}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "api").mkdir()
        for module in MODULES:
            path = f"yomihime_sdk/api/{module}.py"
            source = subprocess.check_output(
                ["git", "show", f"{BASELINE}:{path}"], cwd=ROOT
            )
            provenance["files"][path] = hashlib.sha256(source).hexdigest()
            (root / "api" / f"{module}.py").write_bytes(source)
        source = subprocess.check_output(
            [
                "git",
                "show",
                f"{BASELINE}:infrastructure/sqlite/repositories_subscriptions.py",
            ],
            cwd=ROOT,
        )
        provenance["serializer_repository_sha256"] = hashlib.sha256(source).hexdigest()
        tree = ast.parse(source)
        nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.Import)
            or isinstance(node, ast.ImportFrom)
            and node.level == 0
        ]
        nodes += [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in ("_encode", "_dump")
        ]
        serializer = ast.unparse(ast.Module(body=nodes, type_ignores=[]))
        (root / "serializer.py").write_text(serializer, encoding="utf-8")
        provenance["selected_functions"] = ["_encode", "_dump"]
        result = subprocess.check_output(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); exec(sys.argv[2])",
                str(root),
                CHILD,
            ],
            cwd=ROOT,
        )
    events = json.loads(result)
    OUTPUT.joinpath("events.json").write_text(
        json.dumps(events, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    provenance["events_sha256"] = hashlib.sha256(
        OUTPUT.joinpath("events.json").read_bytes()
    ).hexdigest()
    OUTPUT.joinpath("provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
