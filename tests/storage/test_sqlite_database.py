import asyncio
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.infrastructure.sqlite.database import (
    DatabaseCorruptionError,
    SQLiteBusyError,
    SQLiteDatabase,
    SQLiteUnitOfWork,
    SQLiteUnitOfWorkFactory,
)
from ygl_test_subject.infrastructure.sqlite.migrations import (
    MigrationChecksumError,
    MigrationDiscoveryError,
    discover_migrations,
)


class SQLiteDatabaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _base_only_migrations(self) -> Path:
        migration_dir = self.root / "base-only-migrations"
        migration_dir.mkdir()
        source = (
            Path(__file__).resolve().parents[2]
            / "infrastructure"
            / "sqlite"
            / "migrations"
            / "0000_schema_migrations.sql"
        )
        shutil.copyfile(source, migration_dir / source.name)
        return migration_dir

    def test_reopen_persists_rows_and_migrations(self) -> None:
        path = self.root / "runtime.sqlite3"
        database = SQLiteDatabase(path, migrations_dir=self._base_only_migrations())
        self.assertEqual(database.initialize(), 0)
        self.assertEqual(database.schema_version(), 0)

        async def write() -> None:
            async with await database.begin() as unit:
                unit.execute("CREATE TABLE records (key TEXT PRIMARY KEY, value TEXT)")
                unit.execute("INSERT INTO records VALUES (?, ?)", ("one", "saved"))

        asyncio.run(write())
        connection = database.connect()
        try:
            row = connection.execute(
                "SELECT value FROM records WHERE key = 'one'"
            ).fetchone()
            self.assertEqual(row[0], "saved")
            self.assertIsInstance(row, sqlite3.Row)
        finally:
            connection.close()

    def test_default_migrations_reach_highest_discovered_version(self) -> None:
        path = self.root / "default-migrations.sqlite3"
        database = SQLiteDatabase(path)
        discovered = discover_migrations(database.migrations_dir)
        self.assertGreaterEqual(len(discovered), 2)
        expected_versions = [migration.version for migration in discovered]
        self.assertEqual(database.initialize(), expected_versions[-1])
        connection = database.connect()
        try:
            actual_versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            self.assertEqual(actual_versions, expected_versions)
        finally:
            connection.close()

    def test_subscription_fence_migration_preserves_v80_rows_and_null_stamps(
        self,
    ) -> None:
        path = self.root / "v80.sqlite3"
        migrations = self.root / "v80-migrations"
        migrations.mkdir()
        source = SQLiteDatabase(path).migrations_dir
        for migration in discover_migrations(source):
            if migration.version <= 80:
                shutil.copyfile(migration.path, migrations / migration.path.name)
        old = SQLiteDatabase(path, migrations_dir=migrations)
        self.assertEqual(old.initialize(), 80)
        connection = old.connect()
        try:
            connection.execute(
                "INSERT INTO config_state VALUES ('system','ff14/ff14',7)"
            )
            connection.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) "
                "VALUES ('system','ff14/ff14','gate','malformed-json',7)"
            )
            connection.execute(
                "INSERT INTO b04_collection_jobs(job_key,key_json,scope_kind,config_revision) "
                "VALUES ('old-job','{}','public',7)"
            )
            connection.execute(
                "INSERT INTO b04_subscriptions(subscription_id,revision,owner_id,scope_kind,status,record_json) "
                "VALUES ('old-sub',1,'owner','public','active','{}')"
            )
            connection.execute(
                "INSERT INTO b04_evaluation_states VALUES ('old-sub',3,'{}','{}')"
            )
            connection.execute(
                "INSERT INTO b04_delivery_events(event_key,event_version,subscription_id,"
                "subscription_revision,owner_id,idempotency_key,state,event_json) "
                "VALUES ('old-event',1,'old-sub',1,'owner','old-key','pending','{}')"
            )
            connection.commit()
            tables = (
                "config_entries",
                "b04_collection_jobs",
                "b04_delivery_events",
                "b04_evaluation_states",
            )
            before = {
                table: tuple(connection.execute(f"SELECT * FROM {table}").fetchone())
                for table in tables
            }
        finally:
            connection.close()
        fence_migrations = self.root / "v90-migrations"
        fence_migrations.mkdir()
        for migration in discover_migrations(source):
            if migration.version <= 90:
                shutil.copyfile(migration.path, fence_migrations / migration.path.name)
        upgraded = SQLiteDatabase(path, migrations_dir=fence_migrations)
        self.assertEqual(upgraded.initialize(), 90)
        connection = upgraded.connect()
        try:
            for table in tables:
                row = tuple(connection.execute(f"SELECT * FROM {table}").fetchone())
                with self.subTest(table=table):
                    self.assertEqual(row[: len(before[table])], before[table])
                    self.assertEqual(
                        row[len(before[table]) :],
                        (None,) if table == "config_entries" else (None, None),
                    )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT * FROM subscription_gate_bootstrap"
                    ).fetchone()
                ),
                (1, "pending"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM subscription_gate_initializations"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        # Reapplying migration does not reset a consumed singleton or add stamps.
        connection = upgraded.connect()
        try:
            connection.execute(
                "UPDATE subscription_gate_bootstrap SET phase='complete'"
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(upgraded.initialize(), 90)
        connection = upgraded.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT phase FROM subscription_gate_bootstrap"
                ).fetchone()[0],
                "complete",
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT gate_revision,intent_revision FROM b04_collection_jobs"
                    ).fetchone()
                ),
                (None, None),
            )
        finally:
            connection.close()

        # The historical fence upgrade remains v90; the current full migration
        # advances to v100 without resetting the consumed gate or old data.
        current = SQLiteDatabase(path)
        self.assertEqual(current.initialize(), 100)
        self.assertEqual(current.initialize(), 100)
        connection = current.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT phase FROM subscription_gate_bootstrap"
                ).fetchone()[0],
                "complete",
            )
            for table in tables:
                row = tuple(connection.execute(f"SELECT * FROM {table}").fetchone())
                with self.subTest(current_table=table):
                    self.assertEqual(row[: len(before[table])], before[table])
        finally:
            connection.close()

    def test_uow_exception_rolls_back_and_close_is_idempotent(self) -> None:
        path = self.root / "rollback.sqlite3"
        database = SQLiteDatabase(path)

        async def exercise() -> None:
            async with await database.begin() as unit:
                unit.execute("CREATE TABLE values_table (value TEXT)")
                unit.execute("INSERT INTO values_table VALUES ('committed')")
            unit = await database.begin()
            unit.execute("INSERT INTO values_table VALUES ('rolled-back')")
            await unit.rollback()
            await unit.close()
            await unit.close()

        asyncio.run(exercise())
        connection = database.connect()
        try:
            values = [
                row[0] for row in connection.execute("SELECT value FROM values_table")
            ]
            self.assertEqual(values, ["committed"])
        finally:
            connection.close()

    async def test_lock_timeout_then_recovery_uses_real_connections(self) -> None:
        path = self.root / "locking.sqlite3"
        database = SQLiteDatabase(path, timeout=0.05)
        database.initialize()
        first = database.connect()
        second = database.connect()
        try:
            first.execute("CREATE TABLE lock_table (value TEXT)")
            first.execute("BEGIN IMMEDIATE")
            first.execute("INSERT INTO lock_table VALUES ('held')")
            blocked = SQLiteUnitOfWork(second)
            await blocked.__aenter__()
            with self.assertRaises(SQLiteBusyError):
                blocked.execute("INSERT INTO lock_table VALUES ('blocked')")
            await blocked.rollback()
            await blocked.close()
            first.commit()
            recovered = SQLiteUnitOfWork(database.connect())
            await recovered.__aenter__()
            recovered.execute("INSERT INTO lock_table VALUES ('recovered')")
            await recovered.commit()
            await recovered.close()
            self.assertEqual(
                first.execute("SELECT COUNT(*) FROM lock_table").fetchone()[0], 2
            )
        finally:
            first.close()
            second.close()

    def test_atomic_failed_migration_preserves_previous_rows(self) -> None:
        migration_dir = self.root / "migrations"
        migration_dir.mkdir()
        (migration_dir / "0000_schema_migrations.sql").write_text(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);",
            encoding="utf-8",
        )
        (migration_dir / "0010_records.sql").write_text(
            "CREATE TABLE records (value TEXT); INSERT INTO records VALUES ('old');",
            encoding="utf-8",
        )
        path = self.root / "migration.sqlite3"
        self.assertEqual(
            SQLiteDatabase(path, migrations_dir=migration_dir).initialize(), 10
        )
        (migration_dir / "0020_broken.sql").write_text(
            "CREATE TABLE partial (value TEXT); INSERT INTO missing_table VALUES ('x');",
            encoding="utf-8",
        )
        with self.assertRaises(sqlite3.OperationalError):
            SQLiteDatabase(path, migrations_dir=migration_dir).initialize()
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                10,
            )
            self.assertEqual(
                connection.execute("SELECT value FROM records").fetchone()[0], "old"
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("SELECT * FROM partial")
        finally:
            connection.close()

    def test_missing_applied_migration_is_rejected_and_file_is_preserved(self) -> None:
        migration_dir = self.root / "history-migrations"
        migration_dir.mkdir()
        (migration_dir / "0000_schema_migrations.sql").write_text(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);",
            encoding="utf-8",
        )
        applied = migration_dir / "0010_records.sql"
        applied.write_text(
            "CREATE TABLE records (value TEXT); INSERT INTO records VALUES ('old');",
            encoding="utf-8",
        )
        path = self.root / "history.sqlite3"
        self.assertEqual(
            SQLiteDatabase(path, migrations_dir=migration_dir).initialize(), 10
        )
        before = path.read_bytes()
        applied.unlink()
        with self.assertRaises(MigrationChecksumError):
            SQLiteDatabase(path, migrations_dir=migration_dir).initialize()
        self.assertEqual(path.read_bytes(), before)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                connection.execute("SELECT value FROM records").fetchone()[0], "old"
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                10,
            )
        finally:
            connection.close()

    def test_same_database_rechecks_directory_and_applies_new_migration(self) -> None:
        migration_dir = self.root / "incremental-migrations"
        migration_dir.mkdir()
        (migration_dir / "0000_schema_migrations.sql").write_text(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);",
            encoding="utf-8",
        )
        path = self.root / "incremental.sqlite3"
        database = SQLiteDatabase(path, migrations_dir=migration_dir)
        self.assertEqual(database.initialize(), 0)
        (migration_dir / "0010_records.sql").write_text(
            "CREATE TABLE records (value TEXT); INSERT INTO records VALUES ('new');",
            encoding="utf-8",
        )
        self.assertEqual(database.initialize(), 10)
        self.assertEqual(database.schema_version(), 10)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                connection.execute("SELECT value FROM records").fetchone()[0], "new"
            )
        finally:
            connection.close()

    def test_corruption_is_rejected_without_replacing_original_bytes(self) -> None:
        valid = self.root / "valid.sqlite3"
        SQLiteDatabase(valid).initialize()
        corrupt = self.root / "corrupt.sqlite3"
        shutil.copyfile(valid, corrupt)
        corrupt.write_bytes(corrupt.read_bytes()[:32])
        before = corrupt.read_bytes()
        with self.assertRaises(DatabaseCorruptionError):
            SQLiteDatabase(corrupt).initialize()
        self.assertEqual(corrupt.read_bytes(), before)

        invalid = self.root / "not-sqlite.sqlite3"
        invalid.write_bytes(b"not a sqlite file")
        with self.assertRaises(DatabaseCorruptionError):
            SQLiteDatabase(invalid).initialize()
        self.assertEqual(invalid.read_bytes(), b"not a sqlite file")

    async def test_compare_and_swap_has_one_winner(self) -> None:
        path = self.root / "cas.sqlite3"
        database = SQLiteDatabase(path)
        async with await database.begin() as unit:
            unit.execute(
                "CREATE TABLE versions (id TEXT PRIMARY KEY, revision INTEGER NOT NULL)"
            )
            unit.execute("INSERT INTO versions VALUES ('r', 0)")
        async with await database.begin() as unit:
            unit.compare_and_swap(
                "UPDATE versions SET revision = revision + 1 WHERE id = 'r' AND revision = 0",
                (),
                resource="r",
                expected_revision=0,
            )
        async with await database.begin() as unit:
            with self.assertRaises(RevisionConflict):
                unit.compare_and_swap(
                    "UPDATE versions SET revision = revision + 1 WHERE id = 'r' AND revision = 0",
                    (),
                    resource="r",
                    expected_revision=0,
                    actual_revision=1,
                )

    def test_discovery_order_and_registration_boundaries(self) -> None:
        migration_dir = self.root / "discover"
        migration_dir.mkdir()
        for filename in ("0020_second.sql", "0000_base.sql", "0010_first.sql"):
            (migration_dir / filename).write_text("SELECT 1;", encoding="utf-8")
        migrations = discover_migrations(migration_dir)
        self.assertEqual([migration.version for migration in migrations], [0, 10, 20])
        self.assertEqual(
            [migration.name for migration in migrations], ["base", "first", "second"]
        )
        (migration_dir / "bad.sql").write_text("SELECT 1;", encoding="utf-8")
        with self.assertRaises(MigrationDiscoveryError):
            discover_migrations(migration_dir)

    def test_factory_implements_async_begin(self) -> None:
        path = self.root / "factory.sqlite3"
        factory = SQLiteUnitOfWorkFactory(path)

        async def check() -> None:
            unit = await factory.begin()
            self.assertFalse(unit.closed)
            await unit.rollback()
            await unit.close()

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
