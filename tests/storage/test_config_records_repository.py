"""Real-file SQLite coverage for the B03-S1 repositories."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.contracts.services import (
    ConfigFieldUpdate,
    PersistedConfigPatch,
)
from ygl_test_subject.core.contracts.storage import SecretReceipt, SecretTarget
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
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

from yomihime_game_link_sdk.declarations import (
    ConfigField,
    ConfigUpdateMode,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from yomihime_game_link_sdk.services import ConfigTarget, ModuleHandlers
from yomihime_game_link_sdk.storage import (
    CollectionDescriptor,
    CollectionIndex,
    DeclaredIndexQuery,
    GrantReference,
    OwnerScope,
    OwnershipKind,
    QueryOperator,
    SecretRef,
)


class SubscriptionGateStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = SQLiteDatabase(Path(self.temp.name) / "gates.sqlite3")
        self.database.initialize()
        self.target = validate_contract(ConfigTarget("system", "ff14/ff14"))
        self.now = datetime(2026, 10, 2, 12, tzinfo=UTC)
        self.policy = {self.target: ("subscription_enabled",)}
        self.repository = self._repository()

    async def asyncTearDown(self) -> None:
        await self.database.executor.close()
        self.temp.cleanup()

    def _repository(self, policy=None):
        return SQLiteConfigRepository(
            self.database,
            subscription_gate_fields=self.policy if policy is None else policy,
            clock=lambda: self.now,
        )

    def _sql(self, sql, parameters=()):
        connection = self.database.connect()
        try:
            result = connection.execute(sql, parameters).fetchall()
            connection.commit()
            return [tuple(row) for row in result]
        finally:
            connection.close()

    def _entry(self, field, value_json, revision=7):
        self._sql("INSERT OR IGNORE INTO config_state VALUES ('system','ff14/ff14',7)")
        self._sql(
            "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) "
            "VALUES ('system','ff14/ff14',?,?,?)",
            (field, value_json, revision),
        )

    def _gate_row(self, field="subscription_enabled"):
        return self._sql(
            "SELECT value_json,revision,subscription_transition_at FROM config_entries "
            "WHERE principal_id='system' AND module_id='ff14/ff14' AND field=?",
            (field,),
        )

    def _patch(self, revision, mode, value=None):
        return PersistedConfigPatch(
            revision,
            (ConfigFieldUpdate("subscription_enabled", mode, value=value),),
            (validate_contract(ConfigField("subscription_enabled")),),
            "gate-operation",
            self.target,
        )

    async def test_whole_map_initializes_once_and_preserves_existing_config(self):
        self._entry("region", '"cn"')
        self._entry("logs_alias", '"kept"')
        self._sql(
            "INSERT INTO config_entries(principal_id,module_id,field,secret_token,"
            "secret_principal_id,secret_module_id,secret_field,secret_operation_id,secret_state,revision) "
            "VALUES ('system','ff14/ff14','logs_secret','secret_existing','system',"
            "'ff14/ff14','logs_secret','original-operation','active',7)"
        )
        secret_metadata = (await self.repository.current(self.target)).secret_metadata
        second = validate_contract(ConfigTarget("system", "other/module"))
        policy = {
            self.target: ("subscription_enabled", "another_gate"),
            second: ("gate",),
        }
        repository = self._repository(policy)
        # The immutable policy is independent of subsequent caller mutations.
        policy.clear()
        self.assertTrue(await repository.initialize_subscription_gates())
        self.assertEqual(self._gate_row(), [("true", 8, self.now.isoformat())])
        self.assertEqual(
            self._gate_row("another_gate"), [("true", 8, self.now.isoformat())]
        )
        self.assertEqual(self._gate_row("region"), [('"cn"', 7, None)])
        self.assertEqual(self._gate_row("logs_alias"), [('"kept"', 7, None)])
        self.assertEqual(
            (await repository.current(self.target)).secret_metadata, secret_metadata
        )
        self.assertEqual(
            self._sql(
                "SELECT revision FROM config_state WHERE module_id='other/module'"
            ),
            [(1,)],
        )
        before = self._sql("SELECT * FROM config_entries ORDER BY module_id,field")
        self.now += timedelta(days=1)
        # A completed startup uses read-only SQL even with an unavailable clock.
        repository._clock = lambda: self.fail("complete startup must not read clock")
        with patch.object(
            self.database.executor,
            "run_transaction",
            side_effect=AssertionError("completed bootstrap must only read"),
        ):
            self.assertTrue(await repository.initialize_subscription_gates())
        self.assertEqual(
            before, self._sql("SELECT * FROM config_entries ORDER BY module_id,field")
        )
        self.assertEqual(
            self._sql("SELECT COUNT(*) FROM subscription_gate_initializations"), [(3,)]
        )

    async def test_existing_false_and_malformed_values_are_never_replaced(self):
        self._entry("subscription_enabled", "false")
        self._entry("bad_gate", "not-json")
        repository = self._repository(
            {self.target: ("subscription_enabled", "bad_gate")}
        )
        before = self._sql("SELECT * FROM config_entries ORDER BY field")
        self.assertFalse(await repository.initialize_subscription_gates())
        self.assertEqual(
            before, self._sql("SELECT * FROM config_entries ORDER BY field")
        )
        self.assertEqual(
            self._sql("SELECT phase FROM subscription_gate_bootstrap"), [("complete",)]
        )
        self.assertEqual(self._sql("SELECT revision FROM config_state"), [(7,)])

    async def test_existing_true_only_gets_first_cutoff_metadata(self):
        self._entry("subscription_enabled", " true ")
        self.assertTrue(await self.repository.initialize_subscription_gates())
        self.assertEqual(self._gate_row(), [(" true ", 7, self.now.isoformat())])
        self.assertEqual(self._sql("SELECT revision FROM config_state"), [(7,)])
        self.now += timedelta(days=1)
        self.assertTrue(await self.repository.initialize_subscription_gates())
        self.assertEqual(
            self._gate_row(),
            [(" true ", 7, (self.now - timedelta(days=1)).isoformat())],
        )

    async def test_invalid_non_null_cutoff_is_not_repaired(self):
        self._entry("subscription_enabled", "true")
        self._sql("UPDATE config_entries SET subscription_transition_at='broken'")
        self.assertFalse(await self.repository.initialize_subscription_gates())
        self.assertEqual(self._gate_row(), [("true", 7, "broken")])

    async def test_atomic_failure_rolls_back_all_targets_and_markers(self):
        self._entry("subscription_enabled", "true")
        original = self._sql("SELECT * FROM config_entries")
        second = validate_contract(ConfigTarget("system", "other/module"))
        repository = self._repository(
            {self.target: ("subscription_enabled", "new_gate"), second: ("gate",)}
        )
        self._sql(
            "CREATE TRIGGER reject_second BEFORE INSERT ON subscription_gate_initializations "
            "WHEN NEW.module_id='other/module' BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await repository.initialize_subscription_gates()
        self.assertEqual(self._sql("SELECT * FROM config_entries"), original)
        self.assertEqual(
            self._sql("SELECT * FROM config_state"), [("system", "ff14/ff14", 7)]
        )
        self.assertEqual(
            self._sql("SELECT * FROM subscription_gate_initializations"), []
        )
        self.assertEqual(
            self._sql("SELECT phase FROM subscription_gate_bootstrap"), [("pending",)]
        )
        self._sql("DROP TRIGGER reject_second")
        self.assertTrue(await repository.initialize_subscription_gates())

    async def test_concurrent_startups_consume_one_pending_batch(self):
        self._entry("region", '"cn"')
        outcomes = await asyncio.gather(
            self.repository.initialize_subscription_gates(),
            self._repository().initialize_subscription_gates(),
        )
        self.assertEqual(outcomes, [True, True])
        self.assertEqual(self._sql("SELECT revision FROM config_state"), [(8,)])
        self.assertEqual(
            self._sql("SELECT COUNT(*) FROM subscription_gate_initializations"), [(1,)]
        )

    async def test_pending_with_any_marker_does_not_resume(self):
        self._sql(
            "INSERT INTO subscription_gate_initializations VALUES ('other','other','gate')"
        )
        self.assertFalse(await self.repository.initialize_subscription_gates())
        self.assertEqual(self._sql("SELECT * FROM config_entries"), [])
        self.assertEqual(
            self._sql("SELECT phase FROM subscription_gate_bootstrap"), [("pending",)]
        )

    async def test_unprepared_policy_does_not_consume_pending(self):
        for repository in (SQLiteConfigRepository(self.database), self._repository({})):
            self.assertFalse(await repository.initialize_subscription_gates())
        self.assertEqual(
            self._sql("SELECT phase FROM subscription_gate_bootstrap"), [("pending",)]
        )
        for fields in ((), ("gate", "gate"), ["gate"]):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self._repository({self.target: fields})

    async def test_complete_missing_field_marker_or_new_mapping_never_seeds(self):
        self.assertTrue(await self.repository.initialize_subscription_gates())
        new_policy = self._repository(
            {self.target: ("subscription_enabled", "new_gate")}
        )
        self.assertFalse(await new_policy.initialize_subscription_gates())
        self.assertEqual(self._gate_row("new_gate"), [])
        self.assertEqual(
            self._sql("SELECT COUNT(*) FROM subscription_gate_initializations"), [(1,)]
        )
        self._sql("DELETE FROM config_state")
        self.assertFalse(await self.repository.initialize_subscription_gates())
        self.assertEqual(self._sql("SELECT * FROM config_state"), [])
        self.assertEqual(
            self._sql("SELECT COUNT(*) FROM subscription_gate_initializations"), [(1,)]
        )
        self._entry("subscription_enabled", "true")
        self._sql("DELETE FROM subscription_gate_initializations")
        self.assertFalse(await self.repository.initialize_subscription_gates())
        self.assertEqual(self._gate_row(), [("true", 7, None)])
        self.assertEqual(
            self._sql("SELECT * FROM subscription_gate_initializations"), []
        )

    async def test_missing_or_invalid_bootstrap_is_not_created(self):
        self._sql("DELETE FROM subscription_gate_bootstrap")
        self.assertFalse(await self.repository.initialize_subscription_gates())
        self.assertEqual(self._sql("SELECT * FROM subscription_gate_bootstrap"), [])
        # Construct an invalid persisted phase on the same fixture connection.
        connection = self.database.connect()
        try:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "INSERT INTO subscription_gate_bootstrap VALUES (1,'broken')"
            )
            connection.commit()
        finally:
            connection.close()
        self.assertFalse(await self.repository.initialize_subscription_gates())
        self.assertEqual(
            self._sql("SELECT phase FROM subscription_gate_bootstrap"), [("broken",)]
        )

    async def test_same_value_and_keep_preserve_fence_but_cas_advances(self):
        self.assertTrue(await self.repository.initialize_subscription_gates())
        original = self._gate_row()
        self.now += timedelta(hours=1)
        same = await self.repository.update(
            self.target, self._patch(1, ConfigUpdateMode.REPLACE, True)
        )
        self.assertEqual(same.revision, 2)
        self.assertEqual(self._gate_row(), original)
        kept = await self.repository.update(
            self.target, self._patch(2, ConfigUpdateMode.KEEP)
        )
        self.assertEqual(kept.revision, 3)
        self.assertEqual(self._gate_row(), original)
        paused = await self.repository.update(
            self.target, self._patch(3, ConfigUpdateMode.REPLACE, False)
        )
        self.assertEqual(paused.revision, 4)
        self.assertEqual(self._gate_row(), [("false", 4, self.now.isoformat())])
        self.now += timedelta(hours=1)
        await self.repository.update(
            self.target, self._patch(4, ConfigUpdateMode.REPLACE, True)
        )
        self.assertEqual(self._gate_row(), [("true", 5, self.now.isoformat())])
        with self.assertRaises(RevisionConflict):
            await self.repository.update(
                self.target, self._patch(4, ConfigUpdateMode.REPLACE, False)
            )
        self.assertEqual(self._gate_row(), [("true", 5, self.now.isoformat())])

    async def test_gate_rejects_clear_and_non_bool_without_advancing_cas(self):
        self.assertTrue(await self.repository.initialize_subscription_gates())
        before = self._gate_row()
        for value in (0, 1, "true", (), {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.repository.update(
                    self.target, self._patch(1, ConfigUpdateMode.REPLACE, value)
                )
        with self.assertRaises(ValueError):
            await self.repository.update(
                self.target, self._patch(1, ConfigUpdateMode.CLEAR)
            )
        sensitive_keep = PersistedConfigPatch(
            1,
            (ConfigFieldUpdate("subscription_enabled", ConfigUpdateMode.KEEP),),
            (validate_contract(ConfigField("subscription_enabled", sensitive=True)),),
            "gate-operation",
            self.target,
        )
        with self.assertRaises(ValueError):
            await self.repository.update(self.target, sensitive_keep)
        self.now = datetime(
            2026, 10, 2
        )  # A bad transition clock rolls back overall CAS too.
        with self.assertRaises(ValueError):
            await self.repository.update(
                self.target, self._patch(1, ConfigUpdateMode.REPLACE, False)
            )
        self.assertEqual(self._sql("SELECT revision FROM config_state"), [(1,)])
        self.assertEqual(self._gate_row(), before)


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
            (ConfigFieldUpdate(field, ConfigUpdateMode.REPLACE, value=value),),
            (validate_contract(ConfigField(field)),),
            "operation-1",
            target,
        )

    @staticmethod
    def _records(
        database: SQLiteDatabase, descriptor: CollectionDescriptor
    ) -> tuple[
        SQLiteRecordRepository, Registry, RegistryBackedLookup, RegisteredModule
    ]:
        module = validate_contract(
            ModuleManifest(
                "module-a",
                "module_a",
                ModuleCategory.GAME,
                "tests.fixtures.minimal_module:factory",
                "1.0.0",
                (),
                collections=(descriptor,),
            )
        )
        package = validate_contract(
            PackageManifest(
                "package",
                "1.0.0",
                "2.0",
                (module,),
                "author",
                "MIT",
                "source",
            )
        )
        registry = Registry()
        registry.register_package(
            package, {"module-a": validate_contract(ModuleHandlers({}, {}, {}))}
        )
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
        target = validate_contract(ConfigTarget("user-a", "module-a"))
        first = await self.config.current(target)
        self.assertEqual(first.revision, 1)
        updated = await self.config.update(target, self._ordinary_patch(target, 1))
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.values["region"], "cn")

        secret_target = validate_contract(ConfigTarget("user-a", "module-secret"))
        ref = validate_contract(
            SecretRef("secret_opaque", "user-a", "module-secret", "token", "op-secret")
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
            (ConfigFieldUpdate("token", ConfigUpdateMode.REPLACE, receipt=receipt),),
            (validate_contract(ConfigField("token", sensitive=True)),),
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
        from tests.fixtures.admin_authorization import native_grant

        grant = await native_grant(credentials, AdminOperation.UPDATE_CONFIG)
        target = validate_contract(ConfigTarget("user-authorized", "module-a"))
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
        target = validate_contract(ConfigTarget("user-secret", "module-a"))
        from tests.fixtures.admin_authorization import native_grant

        grant = await native_grant(credentials, AdminOperation.UPDATE_CONFIG)
        receipt = SecretReceipt(
            validate_contract(
                SecretRef("secret_unclaimed", "user-secret", "module-a", "token", "op")
            ),
            SecretTarget("user-secret", "module-a", "token"),
            "op",
            1,
            1,
        )
        patch = PersistedConfigPatch(
            1,
            (ConfigFieldUpdate("token", ConfigUpdateMode.REPLACE, receipt=receipt),),
            (validate_contract(ConfigField("token", sensitive=True)),),
            "op",
            target,
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await self.config.update_authorized(
                target,
                patch,
                grant,
            )
        current = await self.config.current(target)
        self.assertEqual(current.revision, 1)
        self.assertEqual(current.secret_metadata, ())

    async def test_config_owner_isolation_expected_revision_and_rollback(self) -> None:
        target_a = validate_contract(ConfigTarget("user-a", "module-a"))
        target_b = validate_contract(ConfigTarget("user-b", "module-a"))
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
            (
                await self.config.current(
                    validate_contract(ConfigTarget("rollback", "module"))
                )
            ).revision,
            1,
        )

    async def test_records_unique_owner_cas_and_declared_index_paging(self) -> None:
        descriptor = validate_contract(
            CollectionDescriptor(
                "scores",
                1,
                OwnershipKind.USER,
                (validate_contract(CollectionIndex("by-name", "name")),),
            )
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
            validate_contract(
                DeclaredIndexQuery("by-name", QueryOperator.PREFIX, "alice", limit=2)
            )
        )
        self.assertEqual(len(page.records), 2)
        self.assertIsNotNone(page.next_cursor)
        next_page = await first.query(
            validate_contract(
                DeclaredIndexQuery(
                    "by-name",
                    QueryOperator.PREFIX,
                    "alice",
                    limit=2,
                    cursor=page.next_cursor,
                )
            )
        )
        self.assertGreaterEqual(len(next_page.records), 1)
        with self.assertRaises(ValueError):
            await first.query(
                validate_contract(
                    DeclaredIndexQuery("not-declared", QueryOperator.EQUALS, "alice")
                )
            )
        with self.assertRaises(ValueError):
            await first.query(
                validate_contract(
                    DeclaredIndexQuery(
                        "by-name", QueryOperator.EQUALS, "alice", cursor="bad"
                    )
                )
            )

    async def test_record_scope_and_declaration_are_bound(self) -> None:
        descriptor = validate_contract(
            CollectionDescriptor("items", 1, OwnershipKind.AUTHORIZED, ())
        )
        scope = OwnerScope.authorized(
            "user-a", validate_contract(GrantReference("grant-a", 1))
        )
        declared, registry, lookup, registered = self._records(
            self.database, descriptor
        )
        collection = await declared.collection("package/module-a", descriptor, scope)
        other_scope = await declared.collection(
            "package/module-a",
            descriptor,
            OwnerScope.authorized(
                "user-b", validate_contract(GrantReference("grant-a", 1))
            ),
        )
        with self.assertRaises(ValueError):
            await declared.collection(
                "package/module-a",
                validate_contract(
                    CollectionDescriptor("items", 2, OwnershipKind.AUTHORIZED, ())
                ),
                scope,
            )
        with self.assertRaises(ValueError):
            await declared.collection(
                "package/module-a",
                validate_contract(
                    CollectionDescriptor(
                        "items",
                        1,
                        OwnershipKind.AUTHORIZED,
                        (validate_contract(CollectionIndex("by-value", "value")),),
                    )
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
                validate_contract(
                    CollectionDescriptor("other", 1, OwnershipKind.AUTHORIZED)
                ),
                scope,
            )
        mismatched_manifest = validate_contract(
            ModuleManifest(
                "module-a",
                "module_a",
                ModuleCategory.GAME,
                "tests.fixtures.minimal_module:factory",
                "1.0.0",
                (),
                collections=(
                    validate_contract(
                        CollectionDescriptor("items", 2, OwnershipKind.AUTHORIZED)
                    ),
                ),
            )
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
                validate_contract(
                    CollectionDescriptor("items", 2, OwnershipKind.AUTHORIZED)
                ),
                scope,
            )
        fresh_path = Path(self.temp.name) / "undeclared.sqlite3"
        fake_extra = validate_contract(
            CollectionDescriptor("fake-extra", 1, OwnershipKind.AUTHORIZED)
        )
        fake_manifest = validate_contract(
            ModuleManifest(
                "module-a",
                "module_a",
                ModuleCategory.GAME,
                "tests.fixtures.minimal_module:factory",
                "1.0.0",
                (),
                collections=(descriptor, fake_extra),
            )
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
        forged_descriptor = validate_contract(
            CollectionDescriptor(ForgedName("fake"), 1, OwnershipKind.AUTHORIZED)
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
        other_module = validate_contract(
            ModuleManifest(
                "other",
                "other",
                ModuleCategory.GAME,
                "tests.fixtures.minimal_module:factory",
                "1.0.0",
                (),
            )
        )
        registry.register_package(
            validate_contract(
                PackageManifest(
                    "other-package",
                    "1.0.0",
                    "2.0",
                    (other_module,),
                    "author",
                    "MIT",
                    "source",
                )
            ),
            {"other": validate_contract(ModuleHandlers({}, {}, {}))},
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
