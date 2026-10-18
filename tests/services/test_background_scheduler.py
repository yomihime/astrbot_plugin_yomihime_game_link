"""Public background collection is durable without inventing a subscriber."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.admission import AdmissionController, AdmissionError
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.subscriptions import CadenceConfiguration
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import CollectionRunRequest
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteSchedulerRepository,
)
from ygl_test_subject.services.scheduler import (
    ExecutionClaimProofRegistry,
    RescheduleConfiguration,
    SchedulerQuotas,
    SharedCollectionScheduler,
)

from tests.services.test_scheduler import (
    _Binder,
    _Capability,
    _Collector,
    _Grants,
    _JobLinks,
    _Routes,
    _SchedulerModuleInstance,
    _SubscriptionStore,
    _Windows,
)
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    ConfigField,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    SourceDeclaration,
)
from yomihime_game_link_sdk.services import ConfigTarget, ModuleHandlers
from yomihime_game_link_sdk.storage import OwnershipKind
from yomihime_game_link_sdk.subscriptions import (
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ScheduleDescriptor,
    ScheduleTrigger,
)


class BackgroundSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = SQLiteDatabase(Path(self.directory.name) / "background.sqlite3")
        self.database.initialize()
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.collector = _Collector()
        self.collector.finish.set()
        self.config = SQLiteConfigRepository(self.database)
        self.target = ConfigTarget("host-config", "pkg/pricing")
        self.schedule = ScheduleDescriptor(
            "prices",
            1,
            "market",
            1,
            {
                "type": "object",
                "properties": {"item": {"type": "string"}},
                "required": ["item"],
                "additionalProperties": False,
            },
            OwnershipKind.PUBLIC,
            ScheduleTrigger.BACKGROUND,
            21600,
            21600,
            "poll_seconds",
            ({"item": "cn"}, {"item": "global"}),
        )
        self.manifest = validate_contract(
            ModuleManifest(
                "pricing",
                "pricing",
                ModuleCategory.GAME,
                "test.module:Factory",
                "0.1.0",
                (
                    CapabilityDescriptor(
                        "read.data",
                        {"type": "object", "properties": {}, "required": []},
                        InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
                        CapabilityEffect.READ_ONLY,
                    ),
                ),
                schedules=(self.schedule,),
                sources=(SourceDeclaration("market", "example.test"),),
                config_fields=(
                    ConfigField(
                        "poll_seconds",
                        default=21600,
                        value_schema={"type": "integer", "enum": [21600, 43200, 86400]},
                    ),
                ),
            )
        )
        handlers = validate_contract(
            ModuleHandlers({"read.data": _Capability()}, {"prices": self.collector}, {})
        )
        self.registry = Registry()
        self.registry.register_package(
            PackageManifest(
                "pkg", "0.1.0", "2.0", (self.manifest,), "Tests", "AGPL-3.0", "offline"
            ),
            {"pricing": handlers},
        )
        self.issuer = ContextIssuer(clock=lambda: 1.0)
        self.proofs = ExecutionClaimProofRegistry(now=lambda: self.now)
        self.lifecycle = None
        admission = AdmissionController(
            self.registry,
            self.issuer,
            current_run=lambda module: self.lifecycle.current_identity(module),
            is_active=lambda identity: self.lifecycle._is_active_identity(identity),
            health_query=lambda module, capability: self.lifecycle.capability_health(
                module, capability
            ),
            execution_claim_prover=self.proofs,
            utc_clock=lambda: self.now,
        )
        self.lifecycle = LifecycleController(
            self.registry, issuer=self.issuer, admission=admission
        )
        instance = _SchedulerModuleInstance(handlers)
        owned = self.lifecycle.adopt_candidate(
            "pkg", self.manifest, "install", instance
        )
        self.lifecycle.install_dormant("pkg", "pkg/pricing", "install", instance, owned)
        identity, _ = await self.lifecycle.start_candidate("pkg/pricing", "start")
        self.lifecycle.publish_committed_intent(
            "pkg/pricing", "start", identity, True, self.registry.snapshot().revision
        )
        await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "INSERT INTO module_runtime_intents VALUES ('pkg','pricing',1,1,'enable',?)",
                (self.now.isoformat(),),
            ).rowcount
        )
        self.repository = SQLiteSchedulerRepository(
            self.database, utc_clock=lambda: self.now
        )
        self.binder = _Binder()
        self.scheduler = self.make_scheduler()

    def make_scheduler(self):
        async def current_config(_):
            snapshot = await self.config.current_initialized(self.target)
            return (
                snapshot
                if snapshot is not None
                else await self.config.current(self.target)
            )

        return SharedCollectionScheduler(
            registry=self.registry,
            issuer=self.issuer,
            lifecycle=self.lifecycle,
            admission=self.lifecycle.admission,
            execution_claim_proofs=self.proofs,
            binder_for=lambda _: self.binder,
            repository=self.repository,
            subscriptions=_SubscriptionStore(()),
            job_links=_JobLinks(()),
            digest_windows=_Windows(),
            grant_store=_Grants(()),
            routes=_Routes(),
            evaluation_preparer=None,
            cadence=CadenceConfiguration((21600.0, 43200.0, 86400.0)),
            reschedule=RescheduleConfiguration(120, 0, 0, 0),
            quotas=SchedulerQuotas(4, 2, 2, 20, 5, 1, 2),
            now=lambda: self.now,
            monotonic_clock=lambda: 1.0,
            background_config_for=current_config,
        )

    async def asyncTearDown(self):
        await self.database.executor.close()
        self.directory.cleanup()

    async def candidates(self):
        await self.scheduler.reconcile_background_jobs()
        return await self.repository.list_due_jobs(now=self.now, limit=20)

    async def claim(self, candidate):
        module = self.registry.snapshot().module("pkg/pricing")
        return await self.repository.claim_due(
            CollectionRunRequest(
                candidate.key,
                candidate.due_at,
                candidate.cadence_seconds,
                candidate.config_revision,
                module.epoch,
                self.registry.snapshot().revision,
                True,
                30,
            ),
            now=self.now,
        )

    async def test_runs_without_subscribers_and_preserves_due_across_restart(self):
        page = await self.scheduler.run_due_page()
        self.assertEqual(page.processed, 2)
        self.assertEqual(self.collector.calls, 2)
        self.assertEqual(len(self.binder.invocations), 2)
        for view in self.binder.invocations:
            for field in (
                "actor_id",
                "conversation_id",
                "adapter_id",
                "grant_id",
                "capability_id",
                "subscription_id",
                "delivery_route",
            ):
                self.assertIsNone(getattr(view, field))
        self.scheduler = self.make_scheduler()
        self.assertEqual((await self.scheduler.run_due_page()).processed, 0)
        self.now += timedelta(hours=6)
        self.assertEqual((await self.scheduler.run_due_page()).processed, 2)
        self.assertEqual(self.collector.calls, 4)

    async def test_two_workers_claim_once_and_expired_lease_recovers_in_seconds(self):
        candidate = (await self.candidates())[0]
        leases = await asyncio.gather(self.claim(candidate), self.claim(candidate))
        lease = next(item for item in leases if item is not None)
        self.assertEqual(sum(item is not None for item in leases), 1)
        self.assertIsNotNone(lease.background_fence)
        self.assertIsNone(lease.subscription_fence)
        self.assertEqual(lease.expires_at - self.now, timedelta(seconds=30))
        self.now += timedelta(seconds=31)
        self.assertFalse(await self.repository.is_current(lease, now=self.now))
        self.assertIsNotNone(await self.claim(candidate))

    async def test_config_and_intent_changes_fence_old_claim(self):
        candidate = (await self.candidates())[0]
        lease = await self.claim(candidate)
        await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE config_state SET revision=revision+1 WHERE principal_id=? AND module_id=?",
                ("host-config", "pkg/pricing"),
            ).rowcount
        )
        self.assertFalse(await self.repository.is_current(lease, now=self.now))
        refreshed = (await self.candidates())[0]
        fresh = await self.claim(refreshed)
        self.assertIsNotNone(fresh)
        await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE module_runtime_intents SET desired_enabled=0,intent_revision=2 WHERE package_id='pkg'"
            ).rowcount
        )
        self.assertFalse(await self.repository.is_current(fresh, now=self.now))
        self.assertEqual(await self.candidates(), ())

    async def test_subscription_off_does_not_affect_background_and_legacy_claim_cannot_bypass(
        self,
    ):
        candidate = (await self.candidates())[0]
        # Subscription gate bootstrap is still pending; there are no users or subscriptions.
        module = self.registry.snapshot().module("pkg/pricing")
        forged = CollectionRunRequest(
            candidate.key,
            candidate.due_at,
            candidate.cadence_seconds,
            candidate.config_revision,
            module.epoch,
            self.registry.snapshot().revision,
        )
        self.assertIsNone(await self.repository.claim_due(forged, now=self.now))
        lease = await self.claim(candidate)
        self.proofs.prove(lease)
        admitted = self.lifecycle.admission.admit_schedule(lease, "prices")
        self.assertTrue(admitted.background)
        copied = replace(lease, background_fence=None)
        self.proofs.prove(copied)
        with self.assertRaises(AdmissionError):
            self.lifecycle.admission.admit_schedule(copied, "prices")
        self.lifecycle.admission.release(admitted)

    async def test_changed_interval_recomputes_due_from_last_completion(self):
        await self.scheduler.run_due_page()
        self.now += timedelta(hours=1)

        def update(unit):
            unit.execute(
                "UPDATE config_state SET revision=2 WHERE principal_id='host-config' AND module_id='pkg/pricing'"
            )
            unit.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES ('host-config','pkg/pricing','poll_seconds','43200',2)"
            )

        await self.database.executor.run_transaction(update)
        await self.scheduler.reconcile_background_jobs()
        due = await self.database.executor.run_read(
            lambda unit: tuple(
                row[0] for row in unit.execute("SELECT due_at FROM b04_collection_jobs")
            )
        )
        self.assertEqual(
            set(due),
            {(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=12)).isoformat()},
        )

    async def test_removed_declaration_revokes_outstanding_claim(self):
        lease = await self.claim((await self.candidates())[0])
        await self.repository.reconcile_background_jobs((), now=self.now)
        self.assertFalse(await self.repository.is_current(lease, now=self.now))
        self.assertEqual(
            await self.repository.list_due_jobs(now=self.now, limit=20), ()
        )

    async def test_unchanged_pump_performs_no_write_transactions(self):
        await self.scheduler.run_due_page()
        with patch.object(
            self.database.executor,
            "run_transaction",
            side_effect=AssertionError("unexpected pump write"),
        ):
            for _ in range(3):
                self.assertEqual((await self.scheduler.run_due_page()).processed, 0)

    async def test_normalization_collisions_fail_closed(self):
        with patch.object(
            self.collector, "normalize", return_value=NormalizedInput({"item": "same"})
        ):
            self.assertEqual(await self.candidates(), ())
        self.assertEqual(self.collector.calls, 0)

    async def test_failure_preserves_previous_success_and_respects_full_interval(self):
        await self.scheduler.run_due_page()
        before = await self.database.executor.run_read(
            lambda unit: tuple(
                row[0]
                for row in unit.execute(
                    "SELECT observation_id FROM b04_collection_jobs ORDER BY job_key"
                )
            )
        )
        self.collector.fail = True
        self.now += timedelta(hours=6)
        self.assertEqual((await self.scheduler.run_due_page()).processed, 2)
        after = await self.database.executor.run_read(
            lambda unit: tuple(
                row[0]
                for row in unit.execute(
                    "SELECT observation_id FROM b04_collection_jobs ORDER BY job_key"
                )
            )
        )
        self.assertEqual(before, after)
        self.now += timedelta(seconds=121)
        self.assertEqual((await self.scheduler.run_due_page()).processed, 0)

    async def test_cancellation_drains_claim_and_does_not_publish_observation(self):
        candidate = (await self.candidates())[0]
        self.collector.finish.clear()
        task = asyncio.create_task(self.scheduler.run_due_job(candidate))
        await self.collector.started.wait()
        self.assertTrue(await self.scheduler.cancel_local(candidate.key))
        with self.assertRaises(asyncio.CancelledError):
            await task
        count, active = await self.database.executor.run_read(
            lambda unit: (
                unit.execute("SELECT COUNT(*) FROM b04_observations").fetchone()[0],
                unit.execute(
                    "SELECT COUNT(*) FROM b04_collection_jobs WHERE lease_token IS NOT NULL"
                ).fetchone()[0],
            )
        )
        self.assertEqual((count, active), (0, 0))

    async def test_config_change_rejects_background_commit_atomically(self):
        candidate = (await self.candidates())[0]
        lease = await self.claim(candidate)
        observation = Observation(
            "late",
            candidate.key,
            1,
            None,
            self.now,
            ObservationCompleteness.COMPLETE,
            (),
            {"value": 1},
        )
        await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE config_state SET revision=revision+1"
            ).rowcount
        )
        self.assertFalse(await self.repository.commit_observation(lease, observation))
        count = await self.database.executor.run_read(
            lambda unit: unit.execute(
                "SELECT COUNT(*) FROM b04_observations"
            ).fetchone()[0]
        )
        self.assertEqual(count, 0)
