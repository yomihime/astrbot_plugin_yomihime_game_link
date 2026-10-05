"""Source and dependency guards for the real, pinned offline Host contract."""

import importlib.metadata
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.host import astrbot_contract as contract


class AstrBotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Even guard tests require the prepared environment; no implicit fallback.
        cls.source, cls.sources = contract.validate_host_source()
        contract.validate_test_dependencies()

    def test_pinned_contract_loads_actual_dto_auth_and_dispatch(self):
        web, namespace = contract.host_contracts()
        self.assertEqual(Path(web.__file__), self.source / "api/web.py")
        self.assertEqual(web.PluginRequest.__module__, web.__name__)
        for name in (
            "require_dashboard_user",
            "_call_plugin_extension",
            "_match_registered_web_api",
        ):
            self.assertTrue(callable(namespace[name]), name)
        self.assertTrue(callable(namespace["HostAuth"].auth_middleware))

    def test_missing_environment_is_an_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                contract.HostContractEnvironmentError,
                "YGL_TEST_ASTRBOT_SOURCE is not set",
            ):
                contract.host_contracts()

    def test_invalid_source_path_is_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {contract.SOURCE_ENV: temporary}):
                with self.assertRaisesRegex(
                    contract.HostContractEnvironmentError, "checkout/astrbot"
                ):
                    contract.validate_host_source()

    def test_different_git_revision_is_an_error(self):
        real_git = contract._git

        def different_head(root, *arguments):
            if arguments == ("rev-parse", "HEAD"):
                return "0" * 40
            return real_git(root, *arguments)

        with patch.object(contract, "_git", different_head):
            with self.assertRaisesRegex(
                contract.HostContractEnvironmentError, "Host commit mismatch"
            ):
                contract.validate_host_source()

    def _changed_source(self, relative, transform):
        real_read = Path.read_text
        changed_path = self.source.parent / relative

        def read(path, *args, **kwargs):
            value = real_read(path, *args, **kwargs)
            return transform(value) if path == changed_path else value

        return patch.object(Path, "read_text", read)

    def test_different_version_is_an_error(self):
        with self._changed_source(
            "astrbot/__init__.py", lambda text: text.replace('"4.28.2"', '"4.28.3"')
        ):
            with self.assertRaisesRegex(
                contract.HostContractEnvironmentError, "Host version mismatch"
            ):
                contract.validate_host_source()

    def test_source_drift_is_an_error_before_contract_execution(self):
        with self._changed_source(
            "astrbot/api/web.py", lambda text: text + "\n# drift\n"
        ):
            with self.assertRaisesRegex(
                contract.HostContractEnvironmentError, "Host source SHA256 mismatch"
            ):
                contract.host_contracts()

    def test_dirty_index_is_an_error_even_when_working_source_matches(self):
        real_git = contract._git

        def dirty_index(root, *arguments):
            if arguments[0] == "status":
                return "M  astrbot/api/web.py"
            return real_git(root, *arguments)

        with patch.object(contract, "_git", dirty_index):
            with self.assertRaisesRegex(
                contract.HostContractEnvironmentError, "related Host source has"
            ):
                contract.validate_host_source()

    def test_missing_or_different_test_dependency_is_an_error(self):
        with patch.object(
            importlib.metadata,
            "version",
            side_effect=importlib.metadata.PackageNotFoundError("PyJWT"),
        ):
            with self.assertRaisesRegex(
                contract.HostContractEnvironmentError, "missing test dependency PyJWT"
            ):
                contract.validate_test_dependencies()
        with patch.object(importlib.metadata, "version", return_value="0.0.0"):
            with self.assertRaisesRegex(
                contract.HostContractEnvironmentError, "test dependency mismatch"
            ):
                contract.validate_test_dependencies()
