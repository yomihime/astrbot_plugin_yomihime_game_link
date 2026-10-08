"""Fail-closed candidate listing and activation tests."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from api.manifests import EXTENSION_MANIFEST_ABI
from extensions.discovery import DiscoveryRootError, _require_handle_relative_support
from extensions.loader import (
    ActivationUnavailable,
    CandidateState,
    ExtensionLoader,
)


def has_safe_scan() -> bool:
    if os.name == "nt":
        from extensions.windows_fs import WindowsScanError, _check_runtime

        try:
            _check_runtime()
        except WindowsScanError:
            return False
        return True
    try:
        _require_handle_relative_support()
    except DiscoveryRootError:
        return False
    return True


SAFE_SCAN = has_safe_scan()


class LoaderTests(unittest.TestCase):
    def test_valid_candidate_is_inert_and_activation_fails_closed(self) -> None:
        document = {
            "schema_version": 1,
            "package_id": "sample_pkg",
            "package_version": "1.0.0",
            "contract_version": "1.1.0",
            "modules": [],
            "author": "Example",
            "license": "MIT",
            "source": "local",
        }
        with tempfile.TemporaryDirectory() as temporary:
            package_dir = Path(temporary) / "sample"
            package_dir.mkdir()
            (package_dir / EXTENSION_MANIFEST_ABI.filename).write_text(
                json.dumps(document), encoding="utf-8"
            )
            loader = ExtensionLoader()
            self.assertIsNone(loader.candidate("sample_pkg"))
            self.assertEqual(loader.candidates(), ())
            if not SAFE_SCAN:
                with self.assertRaises(DiscoveryRootError):
                    loader.scan(temporary)
                return
            candidates = loader.scan(temporary)
            self.assertEqual(candidates[0].state, CandidateState.DISABLED)
            self.assertEqual(candidates[0].package.enabled, False)
            with self.assertRaisesRegex(ActivationUnavailable, "authorized Core"):
                asyncio.run(loader.enable("sample_pkg", authorization=object()))

    def test_invalid_candidate_is_not_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package_dir = Path(temporary) / "broken"
            package_dir.mkdir()
            (package_dir / EXTENSION_MANIFEST_ABI.filename).write_text(
                "{}", encoding="utf-8"
            )
            if not SAFE_SCAN:
                with self.assertRaises(DiscoveryRootError):
                    ExtensionLoader().scan(temporary)
                return
            candidates = ExtensionLoader().scan(temporary)
            self.assertEqual(candidates[0].state, CandidateState.INVALID)
            self.assertEqual(candidates[0].reason_code, "manifest_invalid")

    def test_enable_always_fails_closed(self) -> None:
        with self.assertRaisesRegex(ActivationUnavailable, "authorized Core"):
            asyncio.run(ExtensionLoader().enable("sample_pkg", authorization=object()))

    def test_backend_is_a_stateless_thin_proxy(self) -> None:
        class Backend:
            def __init__(self) -> None:
                self.candidate_value = object()
                self.calls: list[tuple[object, ...]] = []

            def scan(self, root: object = None):
                self.calls.append(("scan", root))
                return (self.candidate_value,)

            def candidate(self, package_id: str):
                self.calls.append(("candidate", package_id))
                return self.candidate_value

            def candidates(self):
                self.calls.append(("candidates",))
                return (self.candidate_value,)

            async def set_enabled(self, invocation, module_id, enabled, **kwargs):
                self.calls.append(
                    ("set_enabled", invocation, module_id, enabled, kwargs)
                )
                return "status"

        backend = Backend()
        loader = ExtensionLoader(backend)  # type: ignore[arg-type]
        self.assertEqual(loader.scan("root"), (backend.candidate_value,))
        self.assertIs(loader.candidate("sample_pkg"), backend.candidate_value)
        self.assertEqual(loader.candidates(), (backend.candidate_value,))
        invocation = object()
        status = asyncio.run(
            loader.set_enabled(
                invocation,
                "sample_pkg/module",
                True,
                expected_registry_revision=17,
                authorization="grant",
            )
        )
        self.assertEqual(status, "status")
        self.assertEqual(
            backend.calls[-1],
            (
                "set_enabled",
                invocation,
                "sample_pkg/module",
                True,
                {"expected_registry_revision": 17, "authorization": "grant"},
            ),
        )
        # The package-level legacy API remains denied even with an adapter.
        with self.assertRaises(ActivationUnavailable):
            asyncio.run(loader.enable("sample_pkg", authorization=object()))

    def test_inert_backend_absence_never_keeps_scan_catalog(self) -> None:
        loader = ExtensionLoader()
        self.assertIsNone(loader.candidate("p"))
        self.assertEqual(loader.candidates(), ())
        with self.assertRaises(ActivationUnavailable):
            asyncio.run(
                loader.set_enabled(None, "p/m", True, expected_registry_revision=0)
            )


if __name__ == "__main__":
    unittest.main()
