"""Credential failure cannot choose another database or admit secret reads."""

import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from ygl_test_subject.core.ports import SecretOwner
from ygl_test_subject.infrastructure.key_provider import SecretKeyUnavailable
from ygl_test_subject.infrastructure.managed_key_file import ManagedKeyFileError
from ygl_test_subject.infrastructure.managed_key_manager import (
    ManagedKeyConflict,
    ManagedKeyManager,
)
from ygl_test_subject.infrastructure.private_storage import (
    PrivateStorageError,
    resolve_private_storage,
)
from ygl_test_subject.infrastructure.secret_store import (
    SecretStoreUnavailable,
    SQLiteSecretStore,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase

from yomihime_game_link_sdk.storage import SecretRef


class CredentialReadIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_storage_never_reads_inventory_key_or_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = SQLiteDatabase(root / "runtime.sqlite3")
            database.initialize()
            files, environment = Mock(), Mock()
            guard = Mock(side_effect=ManagedKeyFileError())
            manager = ManagedKeyManager(
                database,
                root,
                root / "secrets",
                files=files,
                environment=environment,
                storage_read_guard=guard,
            )
            # Even an already selected manager must recheck before key reads.
            manager._generation = manager._revision = 1
            try:
                with patch.object(manager, "_file_names") as names:
                    for operation in (manager.startup, manager.status):
                        with self.assertRaises(ManagedKeyConflict) as error:
                            await operation()
                        self.assertEqual(error.exception.code, "unsafe_storage_layout")
                    with self.assertRaises(SecretKeyUnavailable):
                        manager.get_key()
                    names.assert_not_called()
                    self.assertFalse(files.mock_calls)
                    self.assertFalse(environment.mock_calls)

                connection = database.connect()
                try:
                    connection.execute(
                        "UPDATE managed_master_key SET source='managed',generation=1,"
                        "revision=1,key_id=?,key_check=? WHERE singleton=1",
                        ("a" * 32, b"s" * 32),
                    )
                    connection.execute(
                        "INSERT INTO secret_receipts(secret_token,principal_id,module_id,"
                        "field,operation_id,expected_config_revision,ledger_revision,"
                        "state,payload_name,key_generation) "
                        "VALUES('secret_fixture','fixture','ff14/ff14','client_secret',"
                        "'op1',0,1,'active','secret_fixture.blob',1)"
                    )
                finally:
                    connection.close()
                store = SQLiteSecretStore(
                    database, root / "secrets", codec=Mock(), key_manager=manager
                )
                ref = SecretRef(
                    "secret_fixture", "fixture", "ff14/ff14", "client_secret", "op1"
                )
                owner = SecretOwner("fixture", "ff14/ff14", "client_secret", "op1")
                with (
                    patch.object(Path, "read_bytes") as read,
                    patch.object(Path, "unlink") as unlink,
                ):
                    for operation in (store.read, store.delete):
                        with self.assertRaises(SecretStoreUnavailable):
                            await operation(ref, owner=owner)
                    read.assert_not_called()
                    unlink.assert_not_called()

                # The same authoritative database still supports ordinary CAS.
                def ordinary_write(unit):
                    unit.execute(
                        "INSERT INTO config_state(principal_id,module_id,revision) "
                        "VALUES('fixture','game_link/core',1)"
                    )
                    return unit.execute(
                        "UPDATE config_state SET revision=2 WHERE principal_id="
                        "'fixture' AND module_id='game_link/core' AND revision=1"
                    ).rowcount

                self.assertEqual(
                    await database.executor.run_transaction(ordinary_write), 1
                )
                self.assertEqual(
                    await database.executor.run_read(
                        lambda unit: unit.execute(
                            "SELECT revision FROM config_state WHERE principal_id="
                            "'fixture' AND module_id='game_link/core'"
                        ).fetchone()[0]
                    ),
                    2,
                )
            finally:
                await database.executor.close()


class SelectorFailureTests(unittest.TestCase):
    def test_selector_safe_read_failure_keeps_ambiguity_code(self):
        from types import SimpleNamespace

        from ygl_test_subject.infrastructure.private_storage import _read_selector

        instance = Mock()
        instance.__truediv__ = Mock(
            return_value=Mock(
                lstat=Mock(
                    return_value=SimpleNamespace(
                        st_mode=stat.S_IFREG, st_nlink=1, st_size=40
                    )
                )
            )
        )
        with patch(
            "ygl_test_subject.infrastructure.windows.managed_key_io.read_key",
            side_effect=ManagedKeyFileError(),
        ):
            with self.assertRaises(PrivateStorageError) as error:
                _read_selector(instance, Path("C:/synthetic-only"))
        self.assertEqual(error.exception.code, "ambiguous_storage")

    def test_resolver_preserves_bounded_selector_failure(self):
        from contextlib import nullcontext
        from types import SimpleNamespace

        fixture_root = Path(__file__).resolve().parent

        with (
            patch(
                "ygl_test_subject.infrastructure.private_storage.os",
                SimpleNamespace(name="nt", path=os.path),
            ),
            patch(
                "ygl_test_subject.infrastructure.windows.private_storage.identity_private_base",
                return_value=fixture_root / "synthetic-only",
            ),
            patch(
                "ygl_test_subject.infrastructure.windows.private_storage.create_private_directory",
                side_effect=lambda root, child: root / child,
            ),
            patch(
                "ygl_test_subject.infrastructure.windows.managed_key_io._pin",
                return_value=nullcontext(),
            ),
            patch(
                "ygl_test_subject.infrastructure.private_storage._read_selector",
                side_effect=PrivateStorageError("ambiguous_storage"),
            ),
            patch(
                "ygl_test_subject.infrastructure.private_storage._has_legacy_inventory"
            ) as inventory,
        ):
            with self.assertRaises(PrivateStorageError) as error:
                resolve_private_storage(fixture_root / "synthetic-data")
            self.assertEqual(error.exception.code, "ambiguous_storage")
            inventory.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows native storage boundary")
class WindowsStorageFaultIsolationTests(unittest.TestCase):
    def setUp(self):
        from ygl_test_subject.infrastructure.windows.private_storage import (
            create_private_directory,
            identity_private_base,
        )

        self.owned = create_private_directory(
            identity_private_base(), "ygl-isolation-synthetic-" + uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, self.owned)
        data = self.owned / "data"
        data.mkdir()
        self.layout = resolve_private_storage(data, private_base=self.owned)
        with self.layout.open_guard():
            connection = sqlite3.connect(self.layout.database_path)
            connection.execute("CREATE TABLE ordinary(value TEXT)")
            connection.execute("INSERT INTO ordinary VALUES('SYNTHETIC ONLY')")
            connection.commit()
            connection.close()

    def test_credential_hardlink_is_isolated_and_guard_remains_latched(self):
        original = self.layout.secret_root / "synthetic.blob"
        original.write_bytes(b"SYNTHETIC ONLY")
        linked = self.layout.temp_root / "synthetic-link"
        os.link(original, linked)
        before = self.layout.database_path.read_bytes()
        with self.layout.open_guard(isolate_credentials=True) as guard:
            with self.assertRaises(ManagedKeyFileError):
                guard.assert_credentials_readable()
            self.assertEqual(self.layout.database_path.read_bytes(), before)
            # A cold restart is required; deleting a link does not revive a
            # handle set that failed its lifetime validation.
            linked.unlink()
            with self.assertRaises(ManagedKeyFileError):
                guard.assert_credentials_readable()
        with self.layout.open_guard() as guard:
            guard.validate()

    def test_database_and_sidecar_hardlinks_never_downgrade(self):
        for name in (
            "runtime.sqlite3",
            "runtime.sqlite3-wal",
            "runtime.sqlite3-shm",
            "runtime.sqlite3-journal",
        ):
            with self.subTest(name=name):
                source = self.layout.storage_root / name
                if not source.exists():
                    source.write_bytes(b"SYNTHETIC SIDECAR")
                linked = self.layout.temp_root / (name + "-link")
                os.link(source, linked)
                try:
                    with self.assertRaises(ManagedKeyFileError):
                        with self.layout.open_guard(isolate_credentials=True):
                            self.fail("database authority cannot downgrade")
                finally:
                    linked.unlink()
                    if name != "runtime.sqlite3":
                        source.unlink()

    def test_missing_database_cannot_downgrade_a_credential_failure(self):
        self.layout.database_path.unlink()
        original = self.layout.secret_root / "synthetic.blob"
        original.write_bytes(b"SYNTHETIC ONLY")
        os.link(original, self.layout.temp_root / "synthetic-link")
        with self.assertRaises(ManagedKeyFileError):
            with self.layout.open_guard(isolate_credentials=True):
                self.fail("must not initialize another empty database")
        self.assertFalse(self.layout.database_path.exists())

    def test_strict_guard_still_rejects_credential_hardlinks(self):
        original = self.layout.secret_root / "synthetic.blob"
        original.write_bytes(b"SYNTHETIC ONLY")
        os.link(original, self.layout.temp_root / "synthetic-link")
        with self.assertRaises(ManagedKeyFileError):
            with self.layout.open_guard():
                self.fail("migration and maintenance remain strict")
