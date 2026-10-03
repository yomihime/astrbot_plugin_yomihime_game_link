from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import AsyncMock, patch

from ygl_test_subject.api.administration import AdminAuthorizationDenied, AdminOperation
from ygl_test_subject.api.manifests import (
    ConfigField,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from ygl_test_subject.api.services import (
    CapabilityHealth,
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
    HealthStatus,
    ModuleHandlers,
    SecretMaterial,
)
from ygl_test_subject.api.storage import (
    CollectionDescriptor,
    CollectionIndex,
    OwnerScope,
    OwnershipKind,
    SecretTarget,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.admission import AdmissionController
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.ports import (
    ModuleNotRegistered,
    ModuleRegistrationSnapshot,
    RevisionConflict,
    SecretOwner,
)
from ygl_test_subject.core.registry import Registry, RegistryError
from ygl_test_subject.infrastructure.secret_store import (
    SecretStoreUnavailable,
    SQLiteSecretStore,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
    SQLiteRecordRepository,
)
from ygl_test_subject.services.admin_authorization import (
    AdminAuthorizationService,
    _digest,
)
from ygl_test_subject.services.configuration import (
    ConfigurationCoordinator,
    ConfigurationRecoveryRequired,
    ConfigurationService,
    ConfigurationUpdateFailed,
)
from ygl_test_subject.services.records import ModuleRecordsService


class _Codec:
    def encrypt(self, value: bytes) -> bytes:
        return b"test-envelope:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        if not value.startswith(b"test-envelope:"):
            raise ValueError("invalid test envelope")
        return value[len(b"test-envelope:") :][::-1]


class _DeleteFailureStore:
    def __init__(self, delegate: SQLiteSecretStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def delete(self, secret_ref, *, owner) -> None:
        raise RuntimeError("test cleanup failure")


class _StageAfterCommitFailureStore:
    def __init__(self, delegate: SQLiteSecretStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def stage(self, *args, **kwargs):
        await self.delegate.stage(*args, **kwargs)
        raise RuntimeError("test stage acknowledgement failure")


class _ClaimFailureStore:
    def __init__(self, delegate: SQLiteSecretStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def claim_for_config(self, *args, **kwargs):
        raise RuntimeError("test claim failure")


class _FinalizeAfterCommitFailureStore:
    def __init__(self, delegate: SQLiteSecretStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def finalize_active(self, *args, **kwargs):
        await self.delegate.finalize_active(*args, **kwargs)
        raise RuntimeError("test finalize acknowledgement failure")


class _FinalizeFailureStore:
    def __init__(self, delegate: SQLiteSecretStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def finalize_active(self, *_args, **_kwargs):
        raise RuntimeError("test finalize failure before commit")


class _BlockingStageStore:
    def __init__(self, delegate: SQLiteSecretStore) -> None:
        self.delegate = delegate
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def stage(self, *args, **kwargs):
        receipt = await self.delegate.stage(*args, **kwargs)
        self.entered.set()
        await self.release.wait()
        return receipt


class _CommitThenPauseRepository:
    def __init__(self, delegate: SQLiteConfigRepository) -> None:
        self.delegate = delegate
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def update_authorized(self, *args, **kwargs):
        snapshot = await self.delegate.update_authorized(*args, **kwargs)
        self.committed.set()
        await self.release.wait()
        return snapshot


class _CommitAfterWriteFailureRepository:
    def __init__(self, delegate: SQLiteConfigRepository) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def update_authorized(self, *args, **kwargs):
        await self.delegate.update_authorized(*args, **kwargs)
        raise RuntimeError("test metadata acknowledgement failure")


class _Lookup:
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


class _PublishUnrelatedAfterCollection:
    def __init__(self, delegate: SQLiteRecordRepository, registry: Registry) -> None:
        self.delegate = delegate
        self.registry = registry
        self.publish_next = False

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def collection(self, *args, **kwargs):
        collection = await self.delegate.collection(*args, **kwargs)
        if self.publish_next:
            self.publish_next = False
            unrelated = ModuleManifest(
                "other",
                "other",
                ModuleCategory.GAME,
                "tests.fixtures.minimal_module:factory",
                "1.0.0",
                (),
            )
            self.registry.register_package(
                PackageManifest(
                    "other-package",
                    "1.0.0",
                    CONTRACT_VERSION,
                    (unrelated,),
                    "author",
                    "MIT",
                    "source",
                ),
                {"other": ModuleHandlers({}, {}, {})},
            )
        return collection


class ConfigRecordsServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = SQLiteDatabase(self.root / "runtime.sqlite3")
        self.config_repository = SQLiteConfigRepository(self.database)
        self.secret_store = SQLiteSecretStore(
            self.database, self.root / "secrets", codec=_Codec()
        )
        self.database.initialize()
        self.admission = AdmissionController(
            Registry(),
            ContextIssuer(),
            current_run=lambda _module_id: None,
            is_active=lambda _identity: False,
            health_query=lambda _module_id, _capability_id: (
                CapabilityHealth(HealthStatus.UNKNOWN),
                0,
            ),
        )
        self.credential_repository = SQLiteAdminCredentialRepository(self.database)
        self.admin_context = type(
            "AdminContext",
            (),
            {"adapter_id": "test", "request_id": "request", "session_id": "session"},
        )()
        self.admin_auth = AdminAuthorizationService(
            self.credential_repository,
            admission=self.admission,
            context_validator=lambda _operation, _invocation, context, _generation: (
                context is self.admin_context
            ),
        )
        self.admin_secret = token_urlsafe(32)
        self._publish_calls: list[tuple[str, object, frozenset[str]]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def asyncSetUp(self) -> None:
        await self.credential_repository.bootstrap(_digest(self.admin_secret))

    def _coordinator(
        self,
        target: ConfigTarget,
        fields,
        repository=None,
        secret_store=None,
        ledger=None,
        subscription_gate_fields=(),
    ):
        return ConfigurationCoordinator(
            target,
            fields,
            repository or self.config_repository,
            secret_store or self.secret_store,
            ledger,
            admission=self.admission,
            validate_admin_grant=lambda grant: self.admin_auth.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=self._publish_config,
            subscription_gate_fields=subscription_gate_fields,
        )

    async def test_mixed_invalid_gate_and_secret_patch_rejects_before_all_staging(self):
        target = ConfigTarget("principal-a", "module-a")
        fields = (
            ConfigField("gate", default=True),
            ConfigField("token", sensitive=True),
        )
        grant = await self.admin_auth.authorize(
            AdminOperation.UPDATE_CONFIG, invocation=None, context=self.admin_context
        )
        for mode, value in (
            (ConfigPatchMode.CLEAR, None),
            (ConfigPatchMode.REPLACE, 0),
            (ConfigPatchMode.REPLACE, 1),
            (ConfigPatchMode.REPLACE, "true"),
            (ConfigPatchMode.REPLACE, {}),
        ):
            for gate_first in (True, False):
                with (
                    self.subTest(mode=mode, value=value, gate_first=gate_first),
                    ExitStack() as stack,
                ):
                    spies = [
                        stack.enter_context(
                            patch.object(
                                self.secret_store, method, new_callable=AsyncMock
                            )
                        )
                        for method in (
                            "stage",
                            "claim_for_config",
                            "finalize_active",
                            "mark_cas_conflict",
                            "pending",
                        )
                    ]
                    spies += [
                        stack.enter_context(
                            patch.object(
                                self.config_repository, method, new_callable=AsyncMock
                            )
                        )
                        for method in ("current", "update_authorized")
                    ]
                    publish = stack.enter_context(patch.object(self, "_publish_config"))
                    coordinator = self._coordinator(
                        target, fields, subscription_gate_fields=("gate",)
                    )
                    gate = ConfigFieldUpdate("gate", mode, value=value)
                    secret = ConfigFieldUpdate(
                        "token",
                        ConfigPatchMode.REPLACE,
                        secret=SecretMaterial(b"synthetic-never-staged"),
                    )
                    updates = (gate, secret) if gate_first else (secret, gate)
                    with self.assertRaises(ValueError):
                        await coordinator.update_admin(
                            target, ConfigPatch(1, updates, fields), grant
                        )
                    for spy in spies:
                        spy.assert_not_called()
                    publish.assert_not_called()
        self.assertEqual(
            await self.secret_store.pending(
                SecretTarget(target.principal_id, target.module_id, "token")
            ),
            (),
        )

    async def test_gate_keeps_existing_bool_and_allows_strict_bool_update(self):
        target = ConfigTarget("principal-a", "module-a")
        fields = (ConfigField("gate", default=True),)
        coordinator = self._coordinator(
            target, fields, subscription_gate_fields=("gate",)
        )
        grant = await self.admin_auth.authorize(
            AdminOperation.UPDATE_CONFIG, invocation=None, context=self.admin_context
        )
        replaced = await coordinator.update_admin(
            target,
            ConfigPatch(
                1,
                (ConfigFieldUpdate("gate", ConfigPatchMode.REPLACE, value=False),),
                fields,
            ),
            grant,
        )
        kept = await coordinator.update_admin(
            target,
            ConfigPatch(2, (ConfigFieldUpdate("gate", ConfigPatchMode.KEEP),), fields),
            grant,
        )
        self.assertIs(replaced.values["gate"], False)
        self.assertIs(kept.values["gate"], False)
        self.assertEqual(kept.revision, 3)

    def _publish_config(
        self, module_id, snapshot, *, changed_fields: frozenset[str]
    ) -> tuple[str, ...]:
        self._publish_calls.append((module_id, snapshot, changed_fields))
        return ()

    async def _admin_update(self, coordinator, patch, *, grant=None):
        snapshot = await coordinator.current()
        if snapshot.target is None:
            self.fail("configuration snapshot omitted its target")
        if grant is None:
            grant = await self.admin_auth.authorize(
                AdminOperation.UPDATE_CONFIG,
                invocation=None,
                context=self.admin_context,
            )
        return await coordinator.update_admin(snapshot.target, patch, grant)

    async def _change_credential_during_stage(self, *, revoke: bool) -> None:
        target = ConfigTarget("principal-a", "module-a")
        blocking_store = _BlockingStageStore(self.secret_store)
        coordinator = self._coordinator(
            target,
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            blocking_store,
        )
        grant = await self.admin_auth.authorize(
            AdminOperation.UPDATE_CONFIG,
            invocation=None,
            context=self.admin_context,
        )
        patch = ConfigPatch(
            1,
            (
                ConfigFieldUpdate(
                    "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"raw")
                ),
            ),
            (ConfigField("token", sensitive=True),),
            "stage-race",
        )
        update = asyncio.create_task(coordinator.update_admin(target, patch, grant))
        await asyncio.wait_for(blocking_store.entered.wait(), timeout=2)
        if revoke:
            await self.admin_auth.revoke(
                self.admin_secret, invocation=None, context=self.admin_context
            )
        else:
            await self.admin_auth.rotate(
                self.admin_secret,
                token_urlsafe(32),
                invocation=None,
                context=self.admin_context,
            )
        blocking_store.release.set()
        with self.assertRaises(AdminAuthorizationDenied):
            await update
        self.assertEqual((await coordinator.current()).revision, 1)
        self.assertEqual(
            await self.secret_store.pending(
                SecretTarget("principal-a", "module-a", "token")
            ),
            (),
        )

    async def test_rotation_while_secret_is_staging_fences_admin_config_write(self):
        await self._change_credential_during_stage(revoke=False)

    async def test_revoke_while_secret_is_staging_fences_admin_config_write(self):
        await self._change_credential_during_stage(revoke=True)

    async def test_committed_claim_is_finalized_on_reopen_after_cancellation(self):
        target = ConfigTarget("principal-a", "module-a")
        pausing_repository = _CommitThenPauseRepository(self.config_repository)
        coordinator = self._coordinator(
            target,
            (ConfigField("token", sensitive=True),),
            pausing_repository,
            self.secret_store,
        )
        patch = ConfigPatch(
            1,
            (
                ConfigFieldUpdate(
                    "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"kept")
                ),
            ),
            (ConfigField("token", sensitive=True),),
            "cancel-after-config-commit",
        )
        update = asyncio.create_task(self._admin_update(coordinator, patch))
        await asyncio.wait_for(pausing_repository.committed.wait(), timeout=2)
        update.cancel()
        await asyncio.sleep(0)
        self.assertFalse(update.done())
        pausing_repository.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await update

        published_module, published_snapshot, changed_fields = self._publish_calls[-1]
        self.assertEqual(published_module, "module-a")
        self.assertEqual(published_snapshot.revision, 2)
        self.assertEqual(changed_fields, frozenset({"token"}))

        reopened = self._coordinator(
            target,
            (ConfigField("token", sensitive=True),),
            SQLiteConfigRepository(self.database),
            self.secret_store,
        )
        self.assertEqual(await reopened.recover_manifest_secrets(), ())
        snapshot = await reopened.current()
        metadata = snapshot.secret_metadata[0]
        self.assertEqual(metadata.state.value, "active")
        self.assertEqual(
            await self.secret_store.read(
                metadata.secret_ref,
                owner=SecretOwner(
                    "principal-a",
                    "module-a",
                    "token",
                    "cancel-after-config-commit",
                ),
            ),
            b"kept",
        )

    async def test_cancellation_after_plain_update_and_secret_clear_publishes_health(
        self,
    ):
        target = ConfigTarget("principal-a", "module-a")
        fields = (ConfigField("region"), ConfigField("token", sensitive=True))
        seed = self._coordinator(
            target, fields, self.config_repository, self.secret_store
        )
        await self._admin_update(
            seed,
            ConfigPatch(
                1,
                (
                    ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="us"),
                    ConfigFieldUpdate(
                        "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"old")
                    ),
                ),
                fields,
                "cancel-seed",
            ),
        )

        async def cancel_after_commit(patch):
            pausing_repository = _CommitThenPauseRepository(self.config_repository)
            coordinator = self._coordinator(
                target, fields, pausing_repository, self.secret_store
            )
            update = asyncio.create_task(self._admin_update(coordinator, patch))
            await asyncio.wait_for(pausing_repository.committed.wait(), timeout=2)
            update.cancel()
            await asyncio.sleep(0)
            self.assertFalse(update.done())
            pausing_repository.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await update
            return coordinator

        plain = await cancel_after_commit(
            ConfigPatch(
                2,
                (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="eu"),),
                fields,
                "cancel-plain",
            )
        )
        plain_snapshot = await plain.current()
        self.assertEqual(plain_snapshot.values["region"], "eu")
        self.assertEqual(self._publish_calls[-1][1], plain_snapshot)
        self.assertEqual(self._publish_calls[-1][2], frozenset({"region"}))

        clearing = await cancel_after_commit(
            ConfigPatch(
                3,
                (ConfigFieldUpdate("token", ConfigPatchMode.CLEAR),),
                fields,
                "cancel-clear",
            )
        )
        cleared_snapshot = await clearing.current()
        self.assertEqual(cleared_snapshot.revision, 4)
        self.assertEqual(cleared_snapshot.secret_metadata[0].state.value, "tombstoned")
        self.assertIsNone(cleared_snapshot.secret_metadata[0].secret_ref)
        self.assertEqual(self._publish_calls[-1][1], cleared_snapshot)
        self.assertEqual(self._publish_calls[-1][2], frozenset({"token"}))

    async def test_reopen_finalizes_referenced_claim_but_cleans_unreferenced_pending(
        self,
    ):
        target = ConfigTarget("principal-a", "module-a")
        fields = (ConfigField("region"), ConfigField("token", sensitive=True))
        coordinator = self._coordinator(
            target,
            fields,
            self.config_repository,
            _FinalizeFailureStore(self.secret_store),
        )
        with self.assertRaises(ConfigurationRecoveryRequired):
            await self._admin_update(
                coordinator,
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"referenced"),
                        ),
                    ),
                    fields,
                    "referenced-claim",
                ),
            )

        # A later unrelated field update advances only the global revision.
        # The token metadata and its receipt stay accurately bound to revision 2.
        plain = self._coordinator(
            target, fields, self.config_repository, self.secret_store
        )
        plain_snapshot = await self._admin_update(
            plain,
            ConfigPatch(
                2,
                (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="eu"),),
                fields,
                "unrelated-region-update",
            ),
        )
        self.assertEqual(plain_snapshot.revision, 3)
        self.assertEqual(plain_snapshot.secret_metadata[0].revision, 2)

        await self.secret_store.stage(
            b"unreferenced",
            target=SecretTarget("principal-a", "module-a", "token"),
            operation_id="unreferenced-stage",
            expected_config_revision=99,
        )
        await self.database.executor.close(timeout=2)
        reopened_database = SQLiteDatabase(self.root / "runtime.sqlite3")
        reopened_secret_store = SQLiteSecretStore(
            reopened_database, self.root / "secrets", codec=_Codec()
        )
        reopened = self._coordinator(
            target,
            fields,
            SQLiteConfigRepository(reopened_database),
            reopened_secret_store,
        )
        self.assertEqual(await reopened.recover_manifest_secrets(), ())
        self.assertEqual(await reopened.recover_manifest_secrets(), ())
        metadata = (await reopened.current()).secret_metadata[0]
        self.assertEqual(metadata.revision, 2)
        self.assertEqual((await reopened.current()).revision, 3)
        self.assertEqual(
            await reopened_secret_store.read(
                metadata.secret_ref,
                owner=SecretOwner(
                    "principal-a", "module-a", "token", "referenced-claim"
                ),
            ),
            b"referenced",
        )
        self.assertEqual(
            await reopened_secret_store.pending(
                SecretTarget("principal-a", "module-a", "token")
            ),
            (),
        )

    async def test_old_secret_cleanup_uses_the_predecessor_inside_shared_gate(self):
        target = ConfigTarget("principal-a", "module-a")
        fields = (ConfigField("token", sensitive=True),)
        initial = self._coordinator(
            target, fields, self.config_repository, self.secret_store
        )
        first = await self._admin_update(
            initial,
            ConfigPatch(
                1,
                (
                    ConfigFieldUpdate(
                        "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"A")
                    ),
                ),
                fields,
                "secret-A",
            ),
        )
        self.assertEqual(first.revision, 2)

        # C's expected revision is 3. Its secret is staged while the visible
        # config is still revision 2, then B commits revision 2 -> 3.
        blocking_store = _BlockingStageStore(self.secret_store)
        update_c = self._coordinator(
            target, fields, self.config_repository, blocking_store
        )
        grant_c = await self.admin_auth.authorize(
            AdminOperation.UPDATE_CONFIG,
            invocation=None,
            context=self.admin_context,
        )
        patch_c = ConfigPatch(
            3,
            (
                ConfigFieldUpdate(
                    "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"C")
                ),
            ),
            fields,
            "secret-C",
        )
        task_c = asyncio.create_task(update_c.update_admin(target, patch_c, grant_c))
        await asyncio.wait_for(blocking_store.entered.wait(), timeout=2)

        update_b = self._coordinator(
            target, fields, self.config_repository, self.secret_store
        )
        snapshot_b = await self._admin_update(
            update_b,
            ConfigPatch(
                2,
                (
                    ConfigFieldUpdate(
                        "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"B")
                    ),
                ),
                fields,
                "secret-B",
            ),
        )
        self.assertEqual(snapshot_b.revision, 3)
        secret_b = snapshot_b.secret_metadata[0].secret_ref

        blocking_store.release.set()
        snapshot_c = await asyncio.wait_for(task_c, timeout=3)
        self.assertEqual(snapshot_c.revision, 4)
        self.assertEqual(
            snapshot_c.secret_metadata[0].secret_ref.operation_id, "secret-C"
        )
        self.assertIsNone(
            await self.secret_store.read(
                secret_b,
                owner=SecretOwner("principal-a", "module-a", "token", "secret-B"),
            )
        )
        self.assertEqual(
            await self.secret_store.pending(
                SecretTarget("principal-a", "module-a", "token")
            ),
            (),
        )

        await self.database.executor.close(timeout=2)
        reopened_database = SQLiteDatabase(self.root / "runtime.sqlite3")
        reopened_secret_store = SQLiteSecretStore(
            reopened_database, self.root / "secrets", codec=_Codec()
        )
        reopened = self._coordinator(
            target,
            fields,
            SQLiteConfigRepository(reopened_database),
            reopened_secret_store,
        )
        self.assertEqual(await reopened.recover_manifest_secrets(), ())
        self.assertEqual(
            await reopened_secret_store.read(
                snapshot_c.secret_metadata[0].secret_ref,
                owner=SecretOwner("principal-a", "module-a", "token", "secret-C"),
            ),
            b"C",
        )

    async def test_unregistered_manifest_config_can_update_without_factory(self):
        target = ConfigTarget("principal-a", "not-registered/module-a")
        coordinator = self._coordinator(
            target, (ConfigField("region"),), self.config_repository, self.secret_store
        )
        updated = await self._admin_update(
            coordinator,
            ConfigPatch(
                1,
                (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="eu"),),
                (ConfigField("region"),),
            ),
        )
        self.assertEqual(updated.values["region"], "eu")
        self.assertEqual(self._publish_calls[-1][0], "not-registered/module-a")
        self.assertEqual(self._publish_calls[-1][2], frozenset({"region"}))

    async def test_legacy_coordinator_writes_are_rejected(self):
        coordinator = ConfigurationCoordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("region"),),
            self.config_repository,
            self.secret_store,
        )
        patch = ConfigPatch(
            1,
            (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="eu"),),
            (ConfigField("region"),),
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await coordinator.update(patch)
        with self.assertRaises(AdminAuthorizationDenied):
            await coordinator.clear(("region",), expected_revision=1)
        self.assertEqual((await coordinator.current()).values, {})

    async def test_admin_update_requires_wiring_and_exact_target(self):
        target = ConfigTarget("principal-a", "module-a")
        patch = ConfigPatch(
            1,
            (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="eu"),),
            (ConfigField("region"),),
        )
        unwired = ConfigurationCoordinator(
            target,
            (ConfigField("region"),),
            self.config_repository,
            self.secret_store,
        )
        grant = await self.admin_auth.authorize(
            AdminOperation.UPDATE_CONFIG,
            invocation=None,
            context=self.admin_context,
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await unwired.update_admin(target, patch, grant)

        wired = self._coordinator(
            target, (ConfigField("region"),), self.config_repository, self.secret_store
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await wired.update_admin(
                ConfigTarget("principal-a", "other/module-a"), patch, grant
            )
        self.assertEqual((await wired.current()).values, {})

    async def test_sensitive_replace_is_staged_claimed_and_finalized(self) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "package/module-a"),
            (ConfigField("region"), ConfigField("token", sensitive=True)),
            self.config_repository,
            self.secret_store,
        )
        await self._admin_update(
            service,
            ConfigPatch(
                1,
                (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="cn"),),
                (ConfigField("region"),),
            ),
        )
        updated = await self._admin_update(
            service,
            ConfigPatch(
                2,
                (
                    ConfigFieldUpdate(
                        "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"raw")
                    ),
                ),
                (ConfigField("token", sensitive=True),),
                "replace-token",
            ),
        )
        self.assertEqual(updated.revision, 3)
        self.assertNotIn("token", updated.values)
        self.assertEqual(updated.secret_metadata[0].state.value, "active")
        self.assertEqual(
            await service.current(),
            await ConfigurationService(
                ConfigTarget("principal-a", "package/module-a"),
                (ConfigField("region"), ConfigField("token", sensitive=True)),
                SQLiteConfigRepository(self.database),
                self.secret_store,
            ).current(),
        )

    async def test_module_facades_expose_only_supported_public_api(self) -> None:
        """Check the supported public surface, not a same-process sandbox."""

        coordinator = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            self.secret_store,
        )
        view = coordinator.view()
        self.assertEqual(await view.current(), await coordinator.current())
        self.assertFalse(hasattr(view, "repository"))
        self.assertFalse(hasattr(view, "secret_store"))
        self.assertFalse(hasattr(view, "ledger"))
        self.assertFalse(hasattr(view, "update"))
        self.assertNotIn("repository", dir(view))
        self.assertNotIn("secret_store", dir(view))
        self.assertNotIn("ledger", dir(view))
        self.assertIsNone(getattr(view, "__dict__", None))

    async def test_sensitive_cas_failure_cleans_staged_payload(self) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            self.secret_store,
        )
        with self.assertRaises(RevisionConflict):
            await self._admin_update(
                service,
                ConfigPatch(
                    99,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"raw"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "cas-failure",
                ),
            )
        self.assertEqual(await service.current(), await service.current())

    async def test_cas_compensation_delete_failure_is_recovery_and_orphan(self) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            _DeleteFailureStore(self.secret_store),
        )
        with self.assertRaises(ConfigurationRecoveryRequired) as context:
            await self._admin_update(
                service,
                ConfigPatch(
                    99,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"raw"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "cas-delete-failure",
                ),
            )
        self.assertTrue(context.exception.transitions)
        pending = await self.secret_store.pending(
            SecretTarget("principal-a", "module-a", "token")
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].state.value, "orphan")

    async def test_missing_codec_is_controlled_unavailable(self) -> None:
        store = SQLiteSecretStore(
            self.database, Path(self.temp.name) / "no-key-secrets"
        )
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            store,
        )
        with self.assertRaises(SecretStoreUnavailable):
            await self._admin_update(
                service,
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"raw"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "no-key",
                ),
            )
        self.assertEqual(list((Path(self.temp.name) / "no-key-secrets").iterdir()), [])

    async def test_stage_ack_failure_reconciles_pending_receipt(self) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            _StageAfterCommitFailureStore(self.secret_store),
        )
        with self.assertRaises(ConfigurationUpdateFailed):
            await self._admin_update(
                service,
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"raw"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "stage-ack-failure",
                ),
            )
        self.assertEqual(
            await self.secret_store.pending(
                SecretTarget("principal-a", "module-a", "token")
            ),
            (),
        )

    async def test_claim_failure_does_not_call_config_cas_or_leave_pending(
        self,
    ) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            _ClaimFailureStore(self.secret_store),
        )
        with self.assertRaises(ConfigurationUpdateFailed):
            await self._admin_update(
                service,
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"raw"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "claim-failure",
                ),
            )
        self.assertEqual((await service.current()).revision, 1)
        self.assertEqual(
            await self.secret_store.pending(
                SecretTarget("principal-a", "module-a", "token")
            ),
            (),
        )

    async def test_finalize_failure_is_recovery_and_active_ref_survives_reopen(
        self,
    ) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            _FinalizeAfterCommitFailureStore(self.secret_store),
        )
        with self.assertRaises(ConfigurationRecoveryRequired) as context:
            await self._admin_update(
                service,
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"raw"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "finalize-failure",
                ),
            )
        self.assertTrue(context.exception.transitions)
        reopened = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            SQLiteConfigRepository(self.database),
            self.secret_store,
        )
        self.assertEqual((await reopened.current()).revision, 2)
        self.assertEqual(
            await self.secret_store.pending(
                SecretTarget("principal-a", "module-a", "token")
            ),
            (),
        )
        metadata = (await reopened.current()).secret_metadata[0]
        self.assertIsNotNone(metadata.secret_ref)
        self.assertEqual(
            await self.secret_store.read(
                metadata.secret_ref,
                owner=SecretOwner(
                    "principal-a", "module-a", "token", "finalize-failure"
                ),
            ),
            b"raw",
        )

    async def test_metadata_ack_failure_is_recovery_and_does_not_delete_new_secret(
        self,
    ) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            _CommitAfterWriteFailureRepository(self.config_repository),
            self.secret_store,
        )
        with self.assertRaises(ConfigurationRecoveryRequired) as context:
            await self._admin_update(
                service,
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"raw"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "metadata-ack-failure",
                ),
            )
        self.assertTrue(context.exception.transitions)
        self.assertEqual((await service.current()).revision, 2)

    async def test_clear_commits_tombstone_before_secret_cleanup(self) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            self.secret_store,
        )
        await self._admin_update(
            service,
            ConfigPatch(
                1,
                (
                    ConfigFieldUpdate(
                        "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"raw")
                    ),
                ),
                (ConfigField("token", sensitive=True),),
                "seed",
            ),
        )
        cleared = await self._admin_update(
            service,
            ConfigPatch(
                2,
                (ConfigFieldUpdate("token", ConfigPatchMode.CLEAR),),
                (ConfigField("token", sensitive=True),),
            ),
        )
        self.assertEqual(cleared.revision, 3)
        self.assertEqual(cleared.secret_metadata[0].state.value, "tombstoned")
        self.assertIsNone(cleared.secret_metadata[0].secret_ref)

    async def test_old_secret_delete_failure_is_recovery_state(self) -> None:
        service = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            self.secret_store,
        )
        await self._admin_update(
            service,
            ConfigPatch(
                1,
                (
                    ConfigFieldUpdate(
                        "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"old")
                    ),
                ),
                (ConfigField("token", sensitive=True),),
                "old",
            ),
        )
        failing = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            SQLiteConfigRepository(self.database),
            _DeleteFailureStore(self.secret_store),
        )
        with self.assertRaises(ConfigurationRecoveryRequired) as context:
            await self._admin_update(
                failing,
                ConfigPatch(
                    2,
                    (
                        ConfigFieldUpdate(
                            "token",
                            ConfigPatchMode.REPLACE,
                            secret=SecretMaterial(b"new"),
                        ),
                    ),
                    (ConfigField("token", sensitive=True),),
                    "new",
                ),
            )
        self.assertTrue(context.exception.transitions)
        self.assertEqual((await failing.current()).revision, 3)

    async def test_clear_delete_failure_keeps_tombstone_and_recovery_state(
        self,
    ) -> None:
        seed = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            self.config_repository,
            self.secret_store,
        )
        await self._admin_update(
            seed,
            ConfigPatch(
                1,
                (
                    ConfigFieldUpdate(
                        "token", ConfigPatchMode.REPLACE, secret=SecretMaterial(b"old")
                    ),
                ),
                (ConfigField("token", sensitive=True),),
                "clear-seed",
            ),
        )
        failing = self._coordinator(
            ConfigTarget("principal-a", "module-a"),
            (ConfigField("token", sensitive=True),),
            SQLiteConfigRepository(self.database),
            _DeleteFailureStore(self.secret_store),
        )
        with self.assertRaises(ConfigurationRecoveryRequired):
            await self._admin_update(
                failing,
                ConfigPatch(
                    2,
                    (ConfigFieldUpdate("token", ConfigPatchMode.CLEAR),),
                    (ConfigField("token", sensitive=True),),
                ),
            )
        snapshot = await failing.current()
        self.assertEqual(snapshot.revision, 3)
        self.assertEqual(snapshot.secret_metadata[0].state.value, "tombstoned")

    async def test_records_use_current_registry_declarations_and_fixed_scope(
        self,
    ) -> None:
        descriptor = CollectionDescriptor(
            "items", 1, OwnershipKind.USER, (CollectionIndex("by-name", "name"),)
        )
        manifest = ModuleManifest(
            "module-a",
            "module-a",
            ModuleCategory.GAME,
            "tests.fixtures.minimal_module:factory",
            "1.0.0",
            (),
            collections=(descriptor,),
        )
        registry = Registry()
        registry.register_package(
            PackageManifest(
                "package",
                "1.0.0",
                "1.1.0",
                (manifest,),
                "author",
                "MIT",
                "source",
            ),
            {"module-a": ModuleHandlers({}, {}, {})},
        )
        snapshot = registry.set_enabled("package/module-a", True)
        registered = snapshot.module("package/module-a")
        lookup = _Lookup(registry)
        records = ModuleRecordsService(
            "package/module-a",
            SQLiteRecordRepository(self.database, registered, lookup),
            OwnerScope.user("principal-a"),
            lookup,
        )
        self.assertFalse(hasattr(records, "repository"))
        self.assertFalse(hasattr(records, "registration_lookup"))
        self.assertFalse(hasattr(records, "owner"))
        self.assertFalse(hasattr(records, "scope"))
        self.assertNotIn("repository", dir(records))
        self.assertNotIn("owner", dir(records))
        collection = await records.collection("items")

        unrelated = ModuleManifest(
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
                CONTRACT_VERSION,
                (unrelated,),
                "author",
                "MIT",
                "source",
            ),
            {"other": ModuleHandlers({}, {}, {})},
        )
        await collection.create("one", {"name": "first"})
        self.assertEqual((await collection.get("one")).value["name"], "first")
        with self.assertRaises(ValueError):
            await records.collection("not-declared")
        registry.set_enabled("package/module-a", False)
        with self.assertRaises(ValueError):
            await collection.get("one")

    async def test_records_ignore_unrelated_publish_during_collection_reissue(self):
        descriptor = CollectionDescriptor(
            "items", 1, OwnershipKind.USER, (CollectionIndex("by-name", "name"),)
        )
        manifest = ModuleManifest(
            "module-a",
            "module-a",
            ModuleCategory.GAME,
            "tests.fixtures.minimal_module:factory",
            "1.0.0",
            (),
            collections=(descriptor,),
        )
        registry = Registry()
        registry.register_package(
            PackageManifest(
                "package",
                "1.0.0",
                CONTRACT_VERSION,
                (manifest,),
                "author",
                "MIT",
                "source",
            ),
            {"module-a": ModuleHandlers({}, {}, {})},
        )
        registered = registry.set_enabled("package/module-a", True).module(
            "package/module-a"
        )
        lookup = _Lookup(registry)
        repository = _PublishUnrelatedAfterCollection(
            SQLiteRecordRepository(self.database, registered, lookup), registry
        )
        records = ModuleRecordsService(
            "package/module-a", repository, OwnerScope.user("principal-a"), lookup
        )
        collection = await records.collection("items")

        repository.publish_next = True
        created = await collection.create("one", {"name": "created"})

        self.assertEqual(created.value["name"], "created")
        self.assertEqual((await collection.get("one")).value["name"], "created")


if __name__ == "__main__":
    unittest.main()
