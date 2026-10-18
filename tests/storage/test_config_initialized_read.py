"""The scheduler's frequent config probe must not initialize or write state."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)

from yomihime_game_link_sdk.services import ConfigTarget


class InitializedConfigReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_existing_snapshot_use_only_reads_and_keep_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            database = SQLiteDatabase(Path(root) / "config.sqlite3")
            repository = SQLiteConfigRepository(database)
            target = ConfigTarget("system", "module-a")
            try:
                await database.executor.initialize()
                with (
                    patch.object(
                        database.executor,
                        "run_transaction",
                        side_effect=AssertionError("write"),
                    ),
                    patch.object(
                        repository,
                        "_ensure_state",
                        side_effect=AssertionError("initialization"),
                    ),
                ):
                    self.assertIsNone(await repository.current_initialized(target))
                self.assertEqual(
                    await database.executor.run_read(
                        lambda unit: unit.execute(
                            "SELECT COUNT(*) FROM config_state"
                        ).fetchone()[0]
                    ),
                    0,
                )
                await repository.current(target)

                def seed(unit):
                    unit.execute(
                        "INSERT INTO config_entries "
                        "(principal_id,module_id,field,value_json,revision) "
                        "VALUES ('system','module-a','days','7',1)"
                    )
                    unit.execute(
                        "INSERT INTO config_entries "
                        "(principal_id,module_id,field,secret_token,secret_principal_id,"
                        "secret_module_id,secret_field,secret_operation_id,secret_state,revision) "
                        "VALUES ('system','module-a','token','secret_test','system',"
                        "'module-a','token','test-operation','active',1)"
                    )

                await database.executor.run_transaction(seed)
                expected = await repository.current(target)
                self.assertEqual(expected.values["days"], 7)
                self.assertEqual(len(expected.secret_metadata), 1)
                with (
                    patch.object(
                        database.executor,
                        "run_transaction",
                        side_effect=AssertionError("write"),
                    ),
                    patch.object(
                        repository,
                        "_ensure_state",
                        side_effect=AssertionError("initialization"),
                    ),
                ):
                    self.assertEqual(
                        await repository.current_initialized(target), expected
                    )
                    self.assertEqual(
                        await repository.current_initialized(target), expected
                    )
            finally:
                await database.executor.close()


if __name__ == "__main__":
    unittest.main()
