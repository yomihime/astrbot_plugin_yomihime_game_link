"""Real-file SQLite coverage for the B03-S1 repositories."""

import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.api.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ygl_test_subject.api.manifests import (
    ConfigField,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from ygl_test_subject.api.services import (
    ConfigFieldUpdate,
    ConfigPatchMode,
    ConfigTarget,
    ModuleHandlers,
    PersistedConfigPatch,
)
from ygl_test_subject.api.storage import (
    CollectionDescriptor,
    CollectionIndex,
    DeclaredIndexQuery,
    GrantReference,
    OwnerScope,
    OwnershipKind,
    QueryOperator,
    SecretReceipt,
    SecretRef,
    SecretTarget,
)
from ygl_test_subject.core.ports import (
    ModuleNotRegistered,
    ModuleRegistrationSnapshot,
    RevisionConflict,
    UniqueConstraintViolation,
)
from ygl_test_subject.core.registry import RegisteredModule, Registry, RegistryError
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
    SQLiteRecordRepository,
)


class RegistryBackedLookup:
    """The trusted adapter used by tests; it reads the real Registry snapshot."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    async def require_registered(self, module_id: str) -> ModuleRegistrationSnapshot:
        snapshot = self.registry.snapshot()
        try:
            module = snapshot.module(module_id)
        except RegistryError:
            raise ModuleNotRegistered(module_id) from None
        return ModuleRegistrationSnapshot(
            module_id,
            module.enabled,
            snapshot.revision,
            module.epoch,
            module.manifest.collections,
        )


class ConfigRecordsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.database = SQLiteDatabase(self.path)
        self.config = SQLiteConfigRepository(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _ordinary_patch(
        target: ConfigTarget, expected: int, field: str = "region", value: object = "cn"
    ) -> PersistedConfigPatch:
        return PersistedConfigPatch(
            expected,
            (ConfigFieldUpdate(field, ConfigPatchMode.REPLACE, value=value),),
            (ConfigField(field),),
            "operation-1",
            target,
        )

    @staticmethod
    def _records(
        database: SQLiteDatabase, descriptor: CollectionDescriptor
    ) -> tuple[
        SQLiteRecordRepository, Registry, RegistryBackedLookup, RegisteredModule
    ]:
        module = ModuleManifest(
            "module-a",
            "module_a",
            ModuleCategory.GAME,
            "tests.fixtures.minimal_module:factory",
            "1.0.0",
            (),
            collections=(descriptor,),
        )
        package = PackageManifest(
            "package",
            "1.0.0",
            "1.1.0",
            (module,),
            "author",
            "MIT",
            "source",
        )
        registry = Registry()
        registry.register_package(package, {"module-a": ModuleHandlers({}, {}, {})})
        snapshot = registry.set_enabled("package/module-a", True)
        registered = snapshot.module("package/module-a")
        lookup = RegistryBackedLookup(registry)
        return (
            SQLiteRecordRepository(database, registered, lookup),
            registry,
            lookup,
            registered,
        )

    async def test_config_scope_cas_restart_and_sensitive_metadata(self) -> None:
        target = ConfigTarget("user-a", "module-a")
        first = await self.config.current(target)
        self.assertEqual(first.revision, 1)
        updated = await self.config.update(target, self._ordinary_patch(target, 1))
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.values["region"], "cn")

        secret_target = ConfigTarget("user-a", "module-secret")
        ref = SecretRef(
            "secret_opaque", "user-a", "module-secret", "token", "op-secret"
        )
        receipt = SecretReceipt(
            ref,
            SecretTarget("user-a", "module-secret", "token"),
            "op-secret",
            1,
            1,
        )
        sensitive = PersistedConfigPatch(
            1,
            (ConfigFieldUpdate("token", ConfigPatchMode.REPLACE, receipt=receipt),),
            (ConfigField("token", sensitive=True),),
            "op-secret",
            secret_target,
        )
        snapshot = await self.config.update(secret_target, sensitive)
        self.assertNotIn("token", snapshot.values)
        self.assertEqual(snapshot.secret_metadata[0].secret_ref, ref)
        with self.path.open("rb") as handle:
            raw = handle.read()
        self.assertNotIn(b"plain-secret-payload", raw)
        reopened = SQLiteConfigRepository(SQLiteDatabase(self.path))
        self.assertEqual(
            (await reopened.current(target)).values["region"],
            "cn",
        )
        self.assertEqual(
            (await reopened.current(secret_target)).secret_metadata[0].secret_ref, ref
        )

    async def test_authorized_update_rechecks_generation_in_config_transaction(
        self,
    ) -> None:
        await self.database.executor.initialize()
        credentials = SQLiteAdminCredentialRepository(self.database)
        await credentials.bootstrap(b"a" * 32)
        grant = AdminAuthorizationGrant(AdminOperation.UPDATE_CONFIG, 1)
        target = ConfigTarget("user-authorized", "module-a")
        first = await self.config.update_authorized(
            target, self._ordinary_patch(target, 1), grant
        )
        self.assertEqual(first.revision, 2)

        await credentials.rotate(1, b"b" * 32)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.config.update_authorized(
                target, self._ordinary_patch(target, 2, value="blocked"), grant
            )
        self.assertEqual((await self.config.current(target)).values["region"], "cn")

    async def test_authorized_update_rejects_unclaimed_secret_receipt(self) -> None:
        await self.database.executor.initialize()
        credentials = SQLiteAdminCredentialRepository(self.database)
        await credentials.bootstrap(b"c" * 32)
        target = ConfigTarget("user-secret", "module-a")
        receipt = SecretReceipt(
            SecretRef("secret_unclaimed", "user-secret", "module-a", "token", "op"),
            SecretTarget("user-secret", "module-a", "token"),
            "op",
            1,
            1,
        )
        patch = PersistedConfigPatch(
            1,
            (ConfigFieldUpdate("token", ConfigPatchMode.REPLACE, receipt=receipt),),
            (ConfigField("token", sensitive=True),),
            "op",
            target,
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await self.config.update_authorized(
                target,
                patch,
                AdminAuthorizationGrant(AdminOperation.UPDATE_CONFIG, 1),
            )
        current = await self.config.current(target)
        self.assertEqual(current.revision, 1)
        self.assertEqual(current.secret_metadata, ())

    async def test_config_owner_isolation_expected_revision_and_rollback(self) -> None:
        target_a = ConfigTarget("user-a", "module-a")
        target_b = ConfigTarget("user-b", "module-a")
        await self.config.update(target_a, self._ordinary_patch(target_a, 1))
        self.assertEqual((await self.config.current(target_b)).revision, 1)
        with self.assertRaises(RevisionConflict):
            await self.config.update(
                target_a, self._ordinary_patch(target_a, 1, value="stale")
            )
        self.assertEqual((await self.config.current(target_a)).values["region"], "cn")

        try:
            async with self.database.unit_of_work() as unit:
                unit.execute(
                    "INSERT INTO config_state(principal_id,module_id,revision) VALUES (?,?,?)",
                    ("rollback", "module", 1),
                )
                raise RuntimeError("rollback probe")
        except RuntimeError:
            pass
        self.assertEqual(
            (await self.config.current(ConfigTarget("rollback", "module"))).revision,
            1,
        )

    async def test_records_unique_owner_cas_and_declared_index_paging(self) -> None:
        descriptor = CollectionDescriptor(
            "scores",
            1,
            OwnershipKind.USER,
            (CollectionIndex("by-name", "name"),),
        )
        records, _, _, _ = self._records(self.database, descriptor)
        first = await records.collection(
            "package/module-a", descriptor, OwnerScope.user("a")
        )
        second = await records.collection(
            "package/module-a", descriptor, OwnerScope.user("b")
        )
        await first.create("one", {"name": "alice"})
        await second.create("one", {"name": "bob"})
        with self.assertRaises(UniqueConstraintViolation):
            await first.create("one", {"name": "duplicate"})
        self.assertEqual((await first.get("one")).value["name"], "alice")
        self.assertIsNone(await first.get("missing"))
        with self.assertRaises(RevisionConflict):
            await first.replace("one", {"name": "stale"}, expected_revision=7)
        replaced = await first.replace("one", {"name": "alice-2"}, expected_revision=1)
        self.assertEqual(replaced.revision, 2)

        for index in range(2, 5):
            await first.create(f"{index}", {"name": f"alice-{index}"})
        page = await first.query(
            DeclaredIndexQuery("by-name", QueryOperator.PREFIX, "alice", limit=2)
        )
        self.assertEqual(len(page.records), 2)
        self.assertIsNotNone(page.next_cursor)
        next_page = await first.query(
            DeclaredIndexQuery(
                "by-name",
                QueryOperator.PREFIX,
                "alice",
                limit=2,
                cursor=page.next_cursor,
            )
        )
        self.assertGreaterEqual(len(next_page.records), 1)
        with self.assertRaises(ValueError):
            await first.query(
                DeclaredIndexQuery("not-declared", QueryOperator.EQUALS, "alice")
            )
        with self.assertRaises(ValueError):
            await first.query(
                DeclaredIndexQuery(
                    "by-name", QueryOperator.EQUALS, "alice", cursor="bad"
                )
            )

    async def test_record_scope_and_declaration_are_bound(self) -> None:
        descriptor = CollectionDescriptor("items", 1, OwnershipKind.AUTHORIZED, ())
        scope = OwnerScope.authorized("user-a", GrantReference("grant-a", 1))
        declared, registry, lookup, registered = self._records(
            self.database, descriptor
        )
        collection = await declared.collection("package/module-a", descriptor, scope)
        other_scope = await declared.collection(
            "package/module-a",
            descriptor,
            OwnerScope.authorized("user-b", GrantReference("grant-a", 1)),
        )
        with self.assertRaises(ValueError):
            await declared.collection(
                "package/module-a",
                CollectionDescriptor("items", 2, OwnershipKind.AUTHORIZED, ()),
                scope,
            )
        with self.assertRaises(ValueError):
            await declared.collection(
                "package/module-a",
                CollectionDescriptor(
                    "items",
                    1,
                    OwnershipKind.AUTHORIZED,
                    (CollectionIndex("by-value", "value"),),
                ),
                scope,
            )
        with self.assertRaises(ValueError):
            await declared.collection(
                "package/module-a", descriptor, OwnerScope.user("user-a")
            )
        with self.assertRaises(TypeError):
            SQLiteRecordRepository(self.database)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            SQLiteRecordRepository(self.database, registered, None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            await declared.collection("package/module-b", descriptor, scope)
        await collection.create("key", {"ok": True})
        self.assertIsNone(await other_scope.get("key"))
        with self.assertRaises(ValueError):
            await declared.collection(
                "package/module-a",
                CollectionDescriptor("other", 1, OwnershipKind.AUTHORIZED),
                scope,
            )
        mismatched_manifest = ModuleManifest(
            "module-a",
            "module_a",
            ModuleCategory.GAME,
            "tests.fixtures.minimal_module:factory",
            "1.0.0",
            (),
            collections=(CollectionDescriptor("items", 2, OwnershipKind.AUTHORIZED),),
        )
        mismatched = SQLiteRecordRepository(
            self.database,
            RegisteredModule(
                "package/module-a",
                mismatched_manifest,
                registered.handlers,
                True,
                registered.epoch,
            ),
            lookup,
        )
        with self.assertRaises(ValueError):
            await mismatched.collection(
                "package/module-a",
                CollectionDescriptor("items", 2, OwnershipKind.AUTHORIZED),
                scope,
            )
        fresh_path = Path(self.temp.name) / "undeclared.sqlite3"
        fake_extra = CollectionDescriptor("fake-extra", 1, OwnershipKind.AUTHORIZED)
        fake_manifest = ModuleManifest(
            "module-a",
            "module_a",
            ModuleCategory.GAME,
            "tests.fixtures.minimal_module:factory",
            "1.0.0",
            (),
            collections=(descriptor, fake_extra),
        )
        fake_extra_repository = SQLiteRecordRepository(
            SQLiteDatabase(fresh_path),
            RegisteredModule(
                "package/module-a",
                fake_manifest,
                registered.handlers,
                True,
                registered.epoch,
            ),
            lookup,
        )
        with self.assertRaises(ValueError):
            await fake_extra_repository.collection(
                "package/module-a", fake_extra, scope
            )

        class ForgedName(str):
            def __eq__(self, other: object) -> bool:
                return True

        forged_path = Path(self.temp.name) / "forged-descriptor.sqlite3"
        forged_descriptor = CollectionDescriptor(
            ForgedName("fake"), 1, OwnershipKind.AUTHORIZED
        )
        forged_repository = SQLiteRecordRepository(
            SQLiteDatabase(forged_path), registered, lookup
        )
        with self.assertRaises(ValueError):
            await forged_repository.collection(
                "package/module-a", forged_descriptor, scope
            )
        forged_database = SQLiteDatabase(forged_path)
        async with forged_database.unit_of_work() as unit:
            row = unit.execute(
                "SELECT 1 FROM module_collections WHERE module_id=? AND collection=?",
                ("package/module-a", "fake"),
            ).fetchone()
            self.assertIsNone(row)
        other_module = ModuleManifest(
            "other",
            "other",
            ModuleCategory.GAME,
            "tests.fixtures.minimal_module:factory",
            "1.0.0",
            (),
        )
        registry.register_package(
            PackageManifest(
                "other-package",
                "1.0.0",
                "1.1.0",
                (other_module,),
                "author",
                "MIT",
                "source",
            ),
            {"other": ModuleHandlers({}, {}, {})},
        )
        self.assertEqual((await collection.get("key")).value["ok"], True)
        fresh = await declared.collection("package/module-a", descriptor, scope)
        self.assertEqual((await fresh.get("key")).value["ok"], True)
        registry.set_enabled("package/module-a", False)
        with self.assertRaises(ValueError):
            await collection.get("key")

        unregistered = RegisteredModule(
            "foreign-module",
            registered.manifest,
            registered.handlers,
            True,
            registered.epoch,
        )
        foreign = SQLiteRecordRepository(self.database, unregistered, lookup)
        with self.assertRaises(ModuleNotRegistered):
            await foreign.collection("foreign-module", descriptor, scope)


if __name__ == "__main__":
    unittest.main()
