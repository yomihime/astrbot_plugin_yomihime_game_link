"""Private namespace, captured-only factory import and reader ownership tests."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from extensions.factory_resolver import (
    FilesystemFactorySource,
)
from extensions.source_snapshot import SourceBundle, _bundle_digest


def _bundle(files: dict[str, bytes]) -> SourceBundle:
    return SourceBundle(files, _bundle_digest(files), sum(map(len, files.values())))


def _candidate() -> object:
    return SimpleNamespace(
        package=SimpleNamespace(package_id="test_pkg", _provenance=object())
    )


class FactoryResolverTests(unittest.TestCase):
    def test_relative_package_and_circular_imports_use_private_snapshot(self) -> None:
        source = FilesystemFactorySource()
        files = {
            "module.py": (
                b"from .pkg import VALUE\n"
                b"from .pkg.a import marker\n"
                b"class Factory:\n"
                b"    value = VALUE\n"
                b"    async def create(self, services):\n"
                b"        return None\n"
            ),
            "pkg/__init__.py": b"from .a import VALUE\n",
            "pkg/a.py": b"VALUE = 'captured'\nfrom . import b\nmarker = b.VALUE\n",
            "pkg/b.py": b"from . import a\nVALUE = a.VALUE\n",
        }
        with patch(
            "extensions.factory_resolver.capture_source_bundle",
            return_value=_bundle(files),
        ):
            lease = asyncio.run(source.capture(_candidate()))  # type: ignore[arg-type]
        before_path = tuple(sys.path)
        factory = lease.resolve("module:Factory")
        factory_module = sys.modules[factory.__class__.__module__]
        finder = factory_module.__loader__
        self.assertEqual(factory.value, "captured")
        self.assertEqual(
            factory.__class__.__module__.startswith(lease._namespace), True
        )
        self.assertEqual(tuple(sys.path), before_path)
        self.assertTrue(
            all(
                name.startswith(lease._namespace)
                for name in sys.modules
                if name == lease._namespace or name.startswith(lease._namespace + ".")
            )
        )
        namespace = lease._namespace
        lease.release()
        self.assertFalse(
            any(
                name == namespace or name.startswith(namespace + ".")
                for name in sys.modules
            )
        )
        self.assertEqual(source.reserved_bytes, 0)
        self.assertEqual(finder._files, {})
        self.assertEqual(finder._module_paths, {})
        with self.assertRaises(ImportError):
            finder.exec_module(factory_module)

    def test_namespaces_are_isolated_and_missing_private_module_never_falls_back(
        self,
    ) -> None:
        source = FilesystemFactorySource()
        files_a = {
            "module.py": b"class Factory:\n    value = 'a'\n    async def create(self, services): return None\n"
        }
        files_b = {
            "module.py": b"class Factory:\n    value = 'b'\n    async def create(self, services): return None\n"
        }
        with patch(
            "extensions.factory_resolver.capture_source_bundle",
            side_effect=[_bundle(files_a), _bundle(files_b)],
        ):
            first, second = asyncio.run(_capture_pair(source))
        self.assertNotEqual(first._namespace, second._namespace)
        self.assertEqual(first.resolve("module:Factory").value, "a")
        self.assertEqual(second.resolve("module:Factory").value, "b")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / first._namespace
            package.mkdir()
            marker = root / "fallback-ran.marker"
            (package / "absent.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            sys.path.insert(0, str(root))
            try:
                with self.assertRaises(ModuleNotFoundError):
                    first.resolve("absent:Factory")
                self.assertFalse(marker.exists())
            finally:
                sys.path.remove(str(root))
        first.release()
        second.release()
        self.assertEqual(source.reserved_bytes, 0)

    def test_constructor_cannot_raise_fixed_budgets(self) -> None:
        with self.assertRaises(ValueError):
            FilesystemFactorySource(max_file_bytes=2 * 1024 * 1024)

    def test_unauthorized_capture_executes_no_bundle_source(self) -> None:
        source = FilesystemFactorySource()
        marker = "_snapshot_execution_marker"
        files = {
            "module.py": (
                f"import builtins\nbuiltins.{marker} = True\n"
                "class Factory:\n"
                "    async def create(self, services): return None\n"
            ).encode()
        }
        with patch(
            "extensions.factory_resolver.capture_source_bundle",
            return_value=_bundle(files),
        ):
            lease = asyncio.run(source.capture(_candidate()))  # type: ignore[arg-type]
        import builtins

        self.assertFalse(hasattr(builtins, marker))
        lease.release()

    def test_cancel_waits_for_real_reader_and_returns_untransferred_budget(
        self,
    ) -> None:
        source = FilesystemFactorySource(max_package_bytes=1024, max_runtime_bytes=1024)
        started = threading.Event()
        finish = threading.Event()
        bundle = _bundle({"module.py": b""})

        def slow_reader(*args: object) -> SourceBundle:
            started.set()
            finish.wait(5)
            return bundle

        async def exercise() -> None:
            with patch(
                "extensions.factory_resolver.capture_source_bundle",
                side_effect=slow_reader,
            ):
                capture = asyncio.create_task(source.capture(_candidate()))  # type: ignore[arg-type]
                ready = await asyncio.to_thread(started.wait, 2)
                self.assertTrue(ready)
                self.assertEqual(source.reserved_bytes, 1024)
                capture.cancel()
                asyncio.get_running_loop().call_later(0.1, finish.set)
                with self.assertRaises(asyncio.CancelledError):
                    await capture
                self.assertEqual(source.reserved_bytes, 0)

        asyncio.run(exercise())


async def _capture_pair(source: FilesystemFactorySource):
    first = await source.capture(_candidate())  # type: ignore[arg-type]
    second = await source.capture(_candidate())  # type: ignore[arg-type]
    return first, second


if __name__ == "__main__":
    unittest.main()
