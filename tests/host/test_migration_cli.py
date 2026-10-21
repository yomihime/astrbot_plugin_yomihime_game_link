"""The public maintenance CLI offers only the supported read-only plan."""

import contextlib
import io
import unittest
from unittest.mock import patch

from ygl_test_subject.infrastructure.private_storage import PrivateStorageError
from ygl_test_subject.scripts import migrate_private_storage as cli


class MigrationCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_execute_never_resolves_or_touches_storage(self):
        with patch.object(cli, "resolve_private_storage") as resolve:
            with self.assertRaises(PrivateStorageError) as error:
                await cli.run("execute", "C:/synthetic-only")
            self.assertEqual(error.exception.code, "offline_supervisor_unavailable")
            resolve.assert_not_called()

    def test_command_line_rejects_execute_before_filesystem_access(self):
        with patch.object(cli, "resolve_private_storage") as resolve:
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                cli.main(["execute", "--data-root", "C:/synthetic-only"])
            self.assertEqual(error.exception.code, 2)
            resolve.assert_not_called()

    async def test_plan_returns_only_safe_projection(self):
        with (
            patch.object(
                cli, "resolve_private_storage", return_value=object()
            ) as resolve,
            patch.object(cli, "plan_storage_migration") as plan,
        ):
            plan.return_value.safe_projection.return_value = {"source_retained": True}
            self.assertEqual(
                await cli.run("plan", "C:/synthetic-only"), {"source_retained": True}
            )
            resolve.assert_called_once()
            plan.return_value.safe_projection.assert_called_once_with()
