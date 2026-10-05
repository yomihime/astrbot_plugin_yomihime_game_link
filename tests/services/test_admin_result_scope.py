"""Owned resource policy applies to repository and coordinator result boundaries."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.api.administration import AdminAuthorizationDenied, AdminOperation
from ygl_test_subject.api.manifests import ConfigField
from ygl_test_subject.api.services import (
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
    PersistedConfigPatch,
)
from ygl_test_subject.core.admission import AdmissionController
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.services.admin_authorization import AdminAuthorizationService
from ygl_test_subject.services.configuration import (
    ConfigurationCoordinator,
    ConfigurationRecoveryRequired,
)

from tests.fixtures.admin_authorization import native_grant
from tests.services import test_ordinary_admin as ordinary_fixture
from tests.services.test_config_records import _Codec


class AdminResultScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.db = SQLiteDatabase(self.root / "scope.db")
        await self.db.executor.initialize()
        self.repo = SQLiteConfigRepository(self.db)
        self.credentials = SQLiteAdminCredentialRepository(self.db)
        self.auth = AdminAuthorizationService(self.credentials)
        self.target = ConfigTarget("fixture", "allowed/module")
        self.other = ConfigTarget("fixture", "other/module")
        self.resources = {self.target: {"public"}}
        self.source = self.auth.register_source(
            "synthetic-source",
            resources=self.resources,
            operations={AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
        )
        self.fields = (
            ConfigField("public"),
            ConfigField("unrelated"),
            ConfigField("logs_secret", sensitive=True),
        )

        def seed(unit):
            for target, values in (
                (self.target, {"public": "allowed", "unrelated": "OUTSIDE_SCOPE"}),
                (self.other, {"outside": "OTHER_TARGET"}),
            ):
                unit.execute(
                    "INSERT INTO config_state VALUES(?,?,1)",
                    (target.principal_id, target.module_id),
                )
                for field, value in values.items():
                    unit.execute(
                        "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES(?,?,?,?,1)",
                        (
                            target.principal_id,
                            target.module_id,
                            field,
                            json.dumps(value),
                        ),
                    )
            unit.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,secret_token,secret_principal_id,secret_module_id,secret_field,secret_operation_id,secret_state,revision) VALUES(?,?,?,?,?,?,?,?,?,1)",
                (
                    self.target.principal_id,
                    self.target.module_id,
                    "logs_secret",
                    "secret_synthetic_unrelated",
                    "fixture",
                    self.target.module_id,
                    "logs_secret",
                    "synthetic-seed",
                    "active",
                ),
            )

        await self.db.executor.run_transaction(seed)
        self.store = SQLiteSecretStore(self.db, self.root / "secrets", codec=_Codec())
        self.admission = AdmissionController(
            Registry(),
            ContextIssuer(),
            current_run=lambda _: None,
            is_active=lambda _: False,
            health_query=lambda *_: None,
        )
        self.published = []
        self.coordinator = ConfigurationCoordinator(
            self.target,
            self.fields,
            self.repo,
            self.store,
            admission=self.admission,
            validate_admin_grant=lambda grant: self.auth.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=lambda _, snapshot, **kw: self.published.append(snapshot),
        )

    async def asyncTearDown(self):
        self.auth.close()
        await self.db.executor.close()
        self.temp.cleanup()

    async def grant(self, operation):
        request = object()
        context = self.source.issue(
            subject="synthetic",
            request=request,
            expiry=time.time() + 30,
            operations={operation},
            resources=self.resources,
            live=lambda actual: actual is request,
        )
        return await self.auth.authorize(
            operation, invocation=None, context=context, resources=self.resources
        )

    def patch(self):
        return ConfigPatch(
            1,
            (ConfigFieldUpdate("public", ConfigPatchMode.REPLACE, value="new"),),
            self.fields,
        )

    def assert_limited(self, snapshot):
        self.assertEqual(set(snapshot.values), {"public"})
        self.assertEqual(snapshot.secret_metadata, ())
        self.assertNotIn("OUTSIDE_SCOPE", repr(snapshot))
        self.assertNotIn("secret_synthetic_unrelated", repr(snapshot))

    async def test_read_requires_read_operation_and_exact_target_filters_values_metadata(
        self,
    ):
        read = await self.grant(AdminOperation.READ_CONFIG)
        self.assert_limited(
            await self.repo.current(
                self.target, grant=read, operation=AdminOperation.READ_CONFIG
            )
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await self.repo.current(
                self.other, grant=read, operation=AdminOperation.READ_CONFIG
            )
        write = await self.grant(AdminOperation.UPDATE_CONFIG)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.repo.current(
                self.target, grant=write, operation=AdminOperation.UPDATE_CONFIG
            )
        with self.assertRaises(AdminAuthorizationDenied):
            await self.repo.current(
                self.target, grant=write, operation=AdminOperation.READ_CONFIG
            )

    async def test_repository_and_coordinator_success_project_only_result_preserve_internal_publisher(
        self,
    ):
        grant = await self.grant(AdminOperation.UPDATE_CONFIG)
        result = await self.coordinator.update_admin(self.target, self.patch(), grant)
        self.assert_limited(result)
        self.assertEqual(len(self.published), 1)
        self.assertEqual(set(self.published[0].values), {"public", "unrelated"})
        self.assertEqual(len(self.published[0].secret_metadata), 1)
        # A direct repository write must have the same limited return boundary.
        persisted = PersistedConfigPatch(
            2,
            (ConfigFieldUpdate("public", ConfigPatchMode.REPLACE, value="newer"),),
            self.fields,
            "scope-direct",
            self.target,
        )
        self.assert_limited(
            await self.repo.update_authorized(self.target, persisted, grant)
        )

    async def test_cas_acknowledgement_failure_recovery_snapshot_is_projected(self):
        original = self.repo.update_authorized

        async def acknowledgement(*args):
            await original(*args)
            raise RuntimeError("synthetic acknowledgement failure")

        grant = await self.grant(AdminOperation.UPDATE_CONFIG)
        with patch.object(self.repo, "update_authorized", side_effect=acknowledgement):
            with self.assertRaises(ConfigurationRecoveryRequired) as raised:
                await self.coordinator.update_admin(self.target, self.patch(), grant)
        self.assert_limited(raised.exception.snapshot)
        self.assertEqual(
            (await self.repo.current(self.target)).values["unrelated"], "OUTSIDE_SCOPE"
        )

    async def test_publication_failure_and_private_apply_recovery_boundary_are_projected(
        self,
    ):
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic publication failure")

        coordinator = ConfigurationCoordinator(
            self.target,
            self.fields,
            self.repo,
            self.store,
            admission=self.admission,
            validate_admin_grant=lambda grant: self.auth.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=fail,
        )
        grant = await self.grant(AdminOperation.UPDATE_CONFIG)
        with self.assertRaises(ConfigurationRecoveryRequired) as raised:
            await coordinator._apply_authorized(self.patch(), grant)
        self.assert_limited(raised.exception.snapshot)

    async def test_native_and_ungranted_repository_reads_keep_complete_snapshot(self):
        from ygl_test_subject.services.admin_authorization import _digest

        await self.credentials.bootstrap(
            _digest("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        )
        grant = await native_grant(self.credentials, AdminOperation.READ_CONFIG)
        native = await self.repo.current(
            self.target, grant=grant, operation=AdminOperation.READ_CONFIG
        )
        internal = await self.repo.current(self.target)
        self.assertEqual(native, internal)
        self.assertEqual(set(native.values), {"public", "unrelated"})
        self.assertEqual(len(native.secret_metadata), 1)

    async def test_outside_patch_or_read_grant_denied_before_secret_staging(self):
        from ygl_test_subject.api.services import SecretMaterial

        write = await self.grant(AdminOperation.UPDATE_CONFIG)
        outside = ConfigPatch(
            1,
            (
                ConfigFieldUpdate(
                    "logs_secret",
                    ConfigPatchMode.REPLACE,
                    secret=SecretMaterial(b"synthetic-forbidden"),
                ),
            ),
            self.fields,
        )
        with patch.object(self.store, "stage") as stage:
            with self.assertRaises(AdminAuthorizationDenied):
                await self.coordinator.update_admin(self.target, outside, write)
            stage.assert_not_called()
        read = await self.grant(AdminOperation.READ_CONFIG)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.coordinator._apply_authorized(self.patch(), read)
        self.assertEqual((await self.repo.current(self.target)).revision, 1)

    async def test_cleanup_failure_filters_snapshot_and_outside_transition_refs(self):
        from ygl_test_subject.core.ports import SecretCompensationState

        outside = (await self.repo.current(self.target)).secret_metadata[0].secret_ref
        transition = self.coordinator._transition(
            outside, outside.operation_id, SecretCompensationState.RECOVERABLE, 2
        )
        grant = await self.grant(AdminOperation.UPDATE_CONFIG)
        with patch.object(
            ConfigurationCoordinator, "_delete_old", return_value=(transition,)
        ):
            with self.assertRaises(ConfigurationRecoveryRequired) as raised:
                await self.coordinator.update_admin(self.target, self.patch(), grant)
        self.assert_limited(raised.exception.snapshot)
        self.assertEqual(raised.exception.transitions, ())

    async def test_finalize_failure_returns_only_allowed_secret_metadata(self):
        from ygl_test_subject.api.services import SecretMaterial

        from tests.services.test_config_records import _FinalizeFailureStore

        resources = {self.target: {"logs_secret"}}
        source = self.auth.register_source(
            "synthetic-secret-source",
            resources=resources,
            operations={AdminOperation.UPDATE_CONFIG},
        )
        request = object()
        context = source.issue(
            subject="synthetic",
            request=request,
            expiry=time.time() + 30,
            operations={AdminOperation.UPDATE_CONFIG},
            resources=resources,
            live=lambda actual: actual is request,
        )
        grant = await self.auth.authorize(
            AdminOperation.UPDATE_CONFIG,
            invocation=None,
            context=context,
            resources=resources,
        )
        coordinator = ConfigurationCoordinator(
            self.target,
            self.fields,
            self.repo,
            _FinalizeFailureStore(self.store),
            _FinalizeFailureStore(self.store),
            admission=self.admission,
            validate_admin_grant=lambda grant: self.auth.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=lambda *_args, **_kw: (),
        )
        update = ConfigPatch(
            1,
            (
                ConfigFieldUpdate(
                    "logs_secret",
                    ConfigPatchMode.REPLACE,
                    secret=SecretMaterial(b"synthetic-new-secret"),
                ),
            ),
            self.fields,
        )
        with self.assertRaises(ConfigurationRecoveryRequired) as raised:
            await coordinator.update_admin(self.target, update, grant)
        self.assertEqual(dict(raised.exception.snapshot.values), {})
        self.assertEqual(
            [m.field for m in raised.exception.snapshot.secret_metadata],
            ["logs_secret"],
        )
        self.assertNotIn("OUTSIDE_SCOPE", repr(raised.exception.snapshot))
        self.assertTrue(raised.exception.transitions)
        self.assertTrue(
            all(
                t.secret_ref.field == "logs_secret"
                for t in raised.exception.transitions
            )
        )

    async def test_close_after_commit_drains_publisher_and_keeps_limited_result(self):
        original = self.repo.update_authorized

        async def committed_then_close(*args):
            result = await original(*args)
            self.auth.close()
            return result

        grant = await self.grant(AdminOperation.UPDATE_CONFIG)
        with patch.object(
            self.repo, "update_authorized", side_effect=committed_then_close
        ):
            result = await self.coordinator.update_admin(
                self.target, self.patch(), grant
            )
        self.assert_limited(result)
        self.assertEqual(len(self.published), 1)
        self.assertEqual(set(self.published[0].values), {"public", "unrelated"})
        self.assertEqual(len(self.published[0].secret_metadata), 1)
        self.assertEqual((await self.repo.current(self.target)).revision, 2)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.coordinator.update_admin(self.target, self.patch(), grant)

    async def test_close_after_commit_recovery_exception_keeps_captured_scope(self):
        original = self.repo.update_authorized

        async def committed_then_close(*args):
            result = await original(*args)
            self.auth.close()
            return result

        def failed_publication(*args, **kwargs):
            raise RuntimeError("synthetic publisher failure after close")

        coordinator = ConfigurationCoordinator(
            self.target,
            self.fields,
            self.repo,
            self.store,
            admission=self.admission,
            validate_admin_grant=lambda grant: self.auth.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=failed_publication,
        )
        grant = await self.grant(AdminOperation.UPDATE_CONFIG)
        with patch.object(
            self.repo, "update_authorized", side_effect=committed_then_close
        ):
            with self.assertRaises(ConfigurationRecoveryRequired) as raised:
                await coordinator.update_admin(self.target, self.patch(), grant)
        self.assert_limited(raised.exception.snapshot)
        self.assertEqual((await self.repo.current(self.target)).revision, 2)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.repo.current(
                self.target, grant=grant, operation=AdminOperation.READ_CONFIG
            )

    async def test_close_before_cas_refuses_write_despite_captured_result_scope(self):
        async def close_before_write(grant):
            self.auth.close()
            await self.auth.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            )

        coordinator = ConfigurationCoordinator(
            self.target,
            self.fields,
            self.repo,
            self.store,
            admission=self.admission,
            validate_admin_grant=close_before_write,
            publish_config=lambda *args, **kw: self.published.append(args),
        )
        grant = await self.grant(AdminOperation.UPDATE_CONFIG)
        with self.assertRaises(AdminAuthorizationDenied):
            await coordinator.update_admin(self.target, self.patch(), grant)
        self.assertEqual((await self.repo.current(self.target)).revision, 1)
        self.assertEqual(self.published, [])


class FacadeResultScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_limited_ff14_update_does_not_return_subscription_or_secret_field_status(
        self,
    ):
        fixture = ordinary_fixture.OrdinaryAdminTests()
        await fixture.asyncSetUp()
        try:
            summary = await fixture.update(
                fixture.ff14target, {"ff14_calendar_default_days": 3}
            )
            self.assertEqual(set(summary.fields), {"ff14_calendar_default_days"})
            self.assertEqual(summary.sensitive_fields, ())
        finally:
            await fixture.asyncTearDown()
