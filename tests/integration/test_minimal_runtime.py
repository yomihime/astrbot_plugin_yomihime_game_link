"""Failure-injection checks for the offline demo's temporary runtime owner."""

from __future__ import annotations

import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.examples import minimal_runtime
from ygl_test_subject.services.output import OutputStatus


class _FakeCoreRuntime:
    def __init__(
        self,
        *,
        close_mode: str,
        start_error: BaseException | None = None,
        close_error: BaseException | None = None,
        **kwargs,
    ) -> None:
        del kwargs
        self.close_mode = close_mode
        self.start_error = start_error
        self.close_error = close_error
        self.closed = False
        self.close_timeouts: list[float] = []
        self.admin_credential_repository = self
        self.admin_operations = self
        self.registry = SimpleNamespace(snapshot=lambda: SimpleNamespace(revision=7))

    async def start(self):
        if self.start_error is not None:
            raise self.start_error
        return SimpleNamespace(migration_version=80)

    async def bootstrap(self, credential_digest):
        del credential_digest
        return SimpleNamespace(generation=2)

    async def set_enabled(self, *args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            enabled=True,
            lifecycle=SimpleNamespace(value="active"),
            module_id="offline_sample/status",
            epoch=1,
        )

    async def invoke_command(self, *args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            result=SimpleNamespace(status=SimpleNamespace(value="success")),
            output=SimpleNamespace(status=OutputStatus.SENT),
        )

    async def close(self, *, timeout):
        self.close_timeouts.append(timeout)
        if self.close_mode == "raise":
            assert self.close_error is not None
            raise self.close_error
        if self.close_mode == "closed":
            self.closed = True
            return True
        return False


class MinimalRuntimeCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="minimal-runtime-cleanup-")
        self.addCleanup(self.temp.cleanup)
        self.created_roots: list[Path] = []

    def _run_demo(self, runtime: _FakeCoreRuntime) -> str:
        output = io.StringIO()

        def create_root(*, prefix: str, dir: Path) -> str:
            del prefix, dir
            root = Path(self.temp.name) / f"owned-root-{len(self.created_roots)}"
            root.mkdir()
            self.created_roots.append(root)
            return str(root)

        try:
            with (
                patch.object(
                    minimal_runtime.tempfile, "mkdtemp", side_effect=create_root
                ),
                patch.object(
                    minimal_runtime,
                    "extract_installed_sdk_examples",
                    return_value=[SimpleNamespace(package_id="offline_sample")],
                ),
                patch.object(minimal_runtime, "CoreRuntime", return_value=runtime),
                contextlib.redirect_stdout(output),
            ):
                asyncio.run(minimal_runtime._run())
        finally:
            self.last_output = output.getvalue()
        return output.getvalue()

    def test_pending_close_retains_owned_tree_and_reports_retry_boundary(self) -> None:
        runtime = _FakeCoreRuntime(close_mode="pending")

        with self.assertRaisesRegex(RuntimeError, "temporary tree was retained"):
            self._run_demo(runtime)

        root = self.created_roots[0]
        self.assertTrue(root.is_dir())
        self.assertEqual(runtime.close_timeouts, [2.0])
        self.assertIn(str(root), self.last_output)
        self.assertIn("does not persist a retry handle", self.last_output)

    def test_successful_close_removes_only_the_owned_tree(self) -> None:
        runtime = _FakeCoreRuntime(close_mode="closed")

        output = self._run_demo(runtime)

        self.assertEqual(runtime.close_timeouts, [2.0])
        self.assertTrue(runtime.closed)
        self.assertFalse(self.created_roots[0].exists())
        self.assertIn("temporary runtime tree removed: True", output)

    def test_close_error_does_not_mask_original_start_error(self) -> None:
        runtime = _FakeCoreRuntime(
            close_mode="raise",
            start_error=ValueError("original startup failure"),
            close_error=RuntimeError("secondary close failure"),
        )

        with self.assertRaisesRegex(ValueError, "original startup failure"):
            self._run_demo(runtime)

        root = self.created_roots[0]
        self.assertTrue(root.is_dir())
        self.assertEqual(runtime.close_timeouts, [2.0])
        self.assertIn("original startup failure", self.last_output)
        self.assertIn("secondary close failure", self.last_output)
        self.assertIn(str(root), self.last_output)


if __name__ == "__main__":
    unittest.main()
