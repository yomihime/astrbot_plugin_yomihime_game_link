"""Keep page exporter storage inside the fixture's temporary tree."""

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot import runtime
from ygl_test_subject.infrastructure.private_storage import resolve_private_storage


@contextmanager
def fixture_private_storage(fixture_root: Path, data_root: Path):
    if os.name != "nt":
        yield
        return

    from ygl_test_subject.infrastructure.windows.private_storage import (
        create_private_directory,
    )

    private_base = create_private_directory(fixture_root, "private-storage")

    def resolve(path: Path):
        if path != data_root:
            raise AssertionError("private storage requested for a different fixture")
        return resolve_private_storage(path, private_base=private_base)

    # Bind only the directory selection during startup. The real resolver,
    # directory creation and all native security checks still run.
    with patch.object(runtime, "resolve_private_storage", resolve):
        yield
