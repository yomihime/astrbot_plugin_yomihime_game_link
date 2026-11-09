"""Real temporary SQLite worker, queued fencing and crash-journal CAS."""

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from ygl_test_subject.bootstrap.coordinator import get_coordinator
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.runtime_ownership import (
    OwnershipConflict,
    RuntimeOwnership,
)
from ygl_test_subject.services.runtime_effect_gate import (
    EffectRejected,
    RuntimeEffectGate,
    invocation_guard,
)


class RuntimeExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = SQLiteDatabase(Path(self.temp.name) / "test.db")
        self.executor = self.database.executor
        await self.executor.initialize()
        self.kernel = get_coordinator()
        self.scope = (self.temp.name, object())
        result = await self.kernel.acquire_core(self.scope, object(), 0, "a" * 64)
        self.token = result["owner_token"]
        self.core = RuntimeEffectGate(self.kernel, self.scope, self.token, 1)

        def setup(unit):
            unit.connection.execute("CREATE TABLE effects(value TEXT)")
            unit.connection.execute(
                "INSERT INTO runtime_owners VALUES (?,?,1,0,1,1,0,?,?)",
                ("core", self.token, "a" * 64, self.kernel.boot_id),
            )
            unit.connection.execute("INSERT INTO effects VALUES ('unknown')")

        await self.executor.run_internal_transaction(setup)
        self.executor.bind_effect_guard(self.core)

    async def asyncTearDown(self):
        await self.executor.close()

        async def done():
            return True

        self.kernel.sync_fence(
            self.scope,
            self.token,
            done,
            done,
            lambda token, owner: dict(
                owner_token=token,
                tracked_inflight=0,
                cleanup_complete=True,
                durable_fence=True,
                resources_closed=True,
            ),
        )
        await self.kernel.await_quiet(self.scope, self.token)
        self.assertTrue(self.kernel.release_scope(self.scope, self.token))
        self.temp.cleanup()

    async def values(self):
        return await self.executor.run_internal_transaction(
            lambda unit: tuple(
                r[0] for r in unit.connection.execute("SELECT value FROM effects")
            )
        )

    async def test_worker_rejects_queued_but_drains_started_after_cancellation(self):
        entered, release = threading.Event(), threading.Event()

        def blocking(unit):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test barrier timeout")
            unit.connection.execute("INSERT INTO effects VALUES ('started')")

        first = asyncio.create_task(self.executor.run_transaction(blocking))
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        second = asyncio.create_task(
            self.executor.run_transaction(
                lambda unit: unit.connection.execute(
                    "INSERT INTO effects VALUES ('queued')"
                ).rowcount
            )
        )
        await asyncio.sleep(0)
        first.cancel()
        self.core.close()
        await asyncio.sleep(0)
        self.assertFalse(first.done())
        self.assertEqual(2, len(self.kernel._record(self.scope).tickets))
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        with self.assertRaises(EffectRejected):
            await second
        self.assertEqual(("unknown", "started"), await self.values())
        with self.assertRaises(EffectRejected):
            await self.executor.run_transaction(lambda unit: None)

    async def test_same_transaction_stale_owner_and_grant(self):
        await self.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "UPDATE runtime_owners SET token=? WHERE scope=?", ("b" * 64, "core")
            ).rowcount
        )
        with self.assertRaises(EffectRejected):
            await self.executor.run_transaction(
                lambda unit: unit.connection.execute(
                    "INSERT INTO effects VALUES ('bad')"
                ).rowcount
            )
        await self.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "UPDATE runtime_owners SET token=? WHERE scope=?", (self.token, "core")
            ).rowcount
        )
        gate = RuntimeEffectGate(
            self.kernel,
            self.scope,
            self.token,
            1,
            package_id="a",
            package_token="c" * 64,
            package_epoch=1,
            grant_revision=3,
            authority_check=lambda: None,
            policy_assert=lambda unit: None,
        )
        await self.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "INSERT INTO runtime_owners VALUES (?,?,1,1,1,1,4,?,?)",
                ("package:a", "c" * 64, "a" * 64, self.kernel.boot_id),
            ).rowcount
        )
        with invocation_guard(gate):
            with self.assertRaisesRegex(EffectRejected, "grant_revoked"):
                await self.executor.run_transaction(lambda unit: None)
        self.assertEqual(("unknown",), await self.values())

    async def test_policy_assert_runs_in_same_business_transaction(self):
        await self.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "INSERT INTO runtime_owners VALUES (?,?,1,1,1,1,1,?,?)",
                ("package:a", "c" * 64, "a" * 64, self.kernel.boot_id),
            ).rowcount
        )

        def revoked(unit):
            self.assertTrue(unit.in_transaction)
            raise EffectRejected("grant_revoked")

        gate = RuntimeEffectGate(
            self.kernel,
            self.scope,
            self.token,
            1,
            package_id="a",
            package_token="c" * 64,
            package_epoch=1,
            grant_revision=1,
            authority_check=lambda: None,
            policy_assert=revoked,
        )
        with invocation_guard(gate):
            with self.assertRaisesRegex(EffectRejected, "grant_revoked"):
                await self.executor.run_transaction(
                    lambda unit: unit.connection.execute(
                        "INSERT INTO effects VALUES ('bad')"
                    ).rowcount
                )
        self.assertEqual(("unknown",), await self.values())

    async def test_journal_atomic_cas_closed_recovery_and_new_token(self):
        def prepare(unit):
            RuntimeOwnership.prepare(
                unit, "t", "package:a", "b" * 64, to_epoch=1, boot_id="old-boot"
            )
            RuntimeOwnership.phase(unit, "t", "PREPARED", "OLD_FENCED")
            RuntimeOwnership.phase(unit, "t", "OLD_FENCED", "OLD_QUIET")
            return RuntimeOwnership.commit_closed(
                unit,
                "t",
                expected_token=None,
                expected_revision=0,
                core_epoch=1,
                package_epoch=1,
                boot_id="old-boot",
            )

        receipt = await self.executor.run_internal_transaction(prepare)
        journals = await self.executor.run_internal_transaction(
            RuntimeOwnership.recoverable
        )
        self.assertEqual("COMMITTED_CLOSED", journals[0]["phase"])
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.phase(
                unit, "t", "COMMITTED_CLOSED", "PUBLISHED"
            )
        )
        with self.assertRaises(OwnershipConflict):
            await self.executor.run_internal_transaction(
                lambda unit: RuntimeOwnership.open_active(
                    unit, "t", receipt["token"], 1, boot_id="new-boot"
                )
            )

        def replace(unit):
            RuntimeOwnership.prepare(
                unit,
                "t2",
                "package:a",
                "c" * 64,
                from_epoch=1,
                to_epoch=2,
                from_digest="b" * 64,
                from_boot_id="old-boot",
                boot_id="new-boot",
            )
            RuntimeOwnership.fence(
                unit,
                "package:a",
                receipt["token"],
                expected_revision=1,
                transition_id="t2",
            )
            RuntimeOwnership.phase(unit, "t2", "OLD_FENCED", "OLD_QUIET")
            return RuntimeOwnership.commit_closed(
                unit,
                "t2",
                expected_token=receipt["token"],
                expected_revision=2,
                core_epoch=1,
                package_epoch=2,
                boot_id="new-boot",
            )

        new = await self.executor.run_internal_transaction(replace)
        self.assertNotEqual(receipt["token"], new["token"])
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.phase(
                unit, "t2", "COMMITTED_CLOSED", "PUBLISHED"
            )
        )
        with self.assertRaises(OwnershipConflict):
            await self.executor.run_internal_transaction(
                lambda unit: RuntimeOwnership.open_active(
                    unit, "t2", new["token"], 1, boot_id="new-boot"
                )
            )
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.open_active(
                unit, "t2", new["token"], 3, boot_id="new-boot"
            )
        )
        self.assertEqual(("unknown",), await self.values())

    async def test_data_contract_rejects_schema_writes_change(self):
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.check_data_contract(
                unit, "stable_id", 1, 1, (1,)
            )
        )
        with self.assertRaisesRegex(OwnershipConflict, "data_incompatible"):
            await self.executor.run_internal_transaction(
                lambda unit: RuntimeOwnership.check_data_contract(
                    unit, "stable_id", 2, 2, (1, 2)
                )
            )

    async def test_real_publisher_policy_revocation_blocks_business_write(self):
        from ygl_test_subject.infrastructure.sqlite.repositories_extension_policy import (
            PolicyError,
            assert_execution_policy,
        )
        from ygl_test_subject.services.publisher_trust import TrustReceipt

        receipt = TrustReceipt(
            "d" * 64, "e" * 64, "{}", "a", 1, "f" * 64, 1, 0, 1, "stable"
        )

        def setup(unit):
            unit.connection.execute(
                "INSERT INTO runtime_owners VALUES (?,?,1,1,1,1,1,?,?)",
                ("package:a", "c" * 64, "a" * 64, self.kernel.boot_id),
            )
            unit.connection.execute(
                "INSERT INTO extension_package_grants VALUES (?,?,?,?,?,0,1,?)",
                ("a", "d" * 64, "{}", "{}", "f" * 64, "test"),
            )
            unit.connection.execute(
                "INSERT INTO extension_publisher_trust VALUES (?,?,?,0,1,?)",
                ("d" * 64, b"x" * 32, '{"channels":["stable"]}', "test"),
            )

        await self.executor.run_internal_transaction(setup)
        gate = RuntimeEffectGate(
            self.kernel,
            self.scope,
            self.token,
            1,
            package_id="a",
            package_token="c" * 64,
            package_epoch=1,
            grant_revision=1,
            authority_check=lambda: None,
            policy_assert=lambda unit: assert_execution_policy(unit, receipt),
        )
        with invocation_guard(gate):
            await self.executor.run_transaction(
                lambda unit: unit.connection.execute(
                    "INSERT INTO effects VALUES ('permitted')"
                ).rowcount
            )
        # Deliberately bypass memory mutation hook: same-transaction SQL is the
        # independent safety net even if the caller still has a stale receipt.
        await self.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "UPDATE extension_publisher_trust SET revoked=1,revision=2"
            ).rowcount
        )
        with invocation_guard(gate):
            with self.assertRaises(PolicyError):
                await self.executor.run_transaction(
                    lambda unit: unit.connection.execute(
                        "INSERT INTO effects VALUES ('revoked')"
                    ).rowcount
                )
        self.assertEqual(("unknown", "permitted"), await self.values())

    async def test_release_head_failure_rolls_back_owner_and_journal(self):
        def setup(unit):
            RuntimeOwnership.prepare(
                unit,
                "fail",
                "package:a",
                "b" * 64,
                to_epoch=1,
                boot_id=self.kernel.boot_id,
            )
            RuntimeOwnership.phase(unit, "fail", "PREPARED", "OLD_FENCED")
            RuntimeOwnership.phase(unit, "fail", "OLD_FENCED", "OLD_QUIET")

        await self.executor.run_internal_transaction(setup)

        def reject(unit):
            raise OwnershipConflict("release_sequence_conflict")

        with self.assertRaises(OwnershipConflict):
            await self.executor.run_internal_transaction(
                lambda unit: RuntimeOwnership.commit_closed(
                    unit,
                    "fail",
                    expected_token=None,
                    expected_revision=0,
                    core_epoch=1,
                    package_epoch=1,
                    boot_id=self.kernel.boot_id,
                    release_commit=reject,
                )
            )
        rows = await self.executor.run_internal_transaction(
            RuntimeOwnership.recoverable
        )
        self.assertEqual("OLD_QUIET", rows[0]["phase"])
        present = await self.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "SELECT count(*) FROM runtime_owners WHERE scope='package:a'"
            ).fetchone()[0]
        )
        self.assertEqual(0, present)

    async def test_cross_boot_epoch_reset_requires_fenced_exact_prior_boot(self):
        await self.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "UPDATE runtime_owners SET token=?,core_epoch=8,boot_id='prior-boot' WHERE scope='core'",
                ("d" * 64,),
            ).rowcount
        )

        def prepare(unit, **changes):
            args = dict(
                boot_id="current-boot",
                from_boot_id="prior-boot",
                from_digest="a" * 64,
                from_epoch=8,
                to_epoch=1,
            )
            args.update(changes)
            RuntimeOwnership.prepare(unit, "restart", "core", "b" * 64, **args)

        with self.assertRaisesRegex(OwnershipConflict, "old_owner_not_fenced"):
            await self.executor.run_internal_transaction(prepare)
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.fence_previous_boot(unit, "current-boot")
        )
        with self.assertRaisesRegex(OwnershipConflict, "stale_owner"):
            await self.executor.run_internal_transaction(
                lambda unit: prepare(unit, from_epoch=0)
            )
        with self.assertRaisesRegex(OwnershipConflict, "stale_epoch"):
            await self.executor.run_internal_transaction(
                lambda unit: prepare(unit, boot_id="prior-boot")
            )
        await self.executor.run_internal_transaction(prepare)

        def commit(unit, boot):
            RuntimeOwnership.phase(unit, "restart", "PREPARED", "OLD_FENCED")
            RuntimeOwnership.phase(unit, "restart", "OLD_FENCED", "OLD_QUIET")
            return RuntimeOwnership.commit_closed(
                unit,
                "restart",
                expected_token="d" * 64,
                expected_revision=2,
                core_epoch=1,
                package_epoch=0,
                new_token=self.token,
                boot_id=boot,
            )

        with self.assertRaisesRegex(OwnershipConflict, "stale_boot"):
            await self.executor.run_internal_transaction(
                lambda unit: commit(unit, "wrong-boot")
            )
        receipt = await self.executor.run_internal_transaction(
            lambda unit: commit(unit, "current-boot")
        )
        self.assertNotEqual("d" * 64, receipt["token"])
        journal = await self.executor.run_internal_transaction(
            RuntimeOwnership.recoverable
        )
        self.assertEqual(
            (8, 1, "prior-boot", "current-boot"),
            tuple(
                journal[0][key]
                for key in ("from_epoch", "to_epoch", "from_boot_id", "boot_id")
            ),
        )

    async def test_waiting_cleanup_resumes_only_exact_original_phase(self):
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.prepare(
                unit,
                "waiting",
                "package:a",
                "a" * 64,
                to_epoch=1,
                boot_id=self.kernel.boot_id,
            )
        )
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.phase(
                unit,
                "waiting",
                "PREPARED",
                "WAITING_CLEANUP",
                error_code="cleanup_pending",
            )
        )
        with self.assertRaises(OwnershipConflict):
            await self.executor.run_internal_transaction(
                lambda unit: RuntimeOwnership.resume_waiting(
                    unit, "waiting", "OLD_QUIET"
                )
            )
        await self.executor.run_internal_transaction(
            lambda unit: RuntimeOwnership.resume_waiting(unit, "waiting", "PREPARED")
        )
        with self.assertRaises(OwnershipConflict):
            await self.executor.run_internal_transaction(
                lambda unit: RuntimeOwnership.resume_waiting(
                    unit, "waiting", "PREPARED"
                )
            )

    async def test_invalid_data_formats_and_package_ids_rejected(self):
        for schema, writes, readable in (
            (0, 1, (1,)),
            (1, 0, (1,)),
            (True, 1, (1,)),
            (1, 1, ()),
            (1, 1, (True,)),
            (1, 1, (1, 1)),
            (1, 1, (2**53,)),
        ):
            with self.subTest(schema=schema, writes=writes, readable=readable):
                with self.assertRaises(ValueError):
                    await self.executor.run_internal_transaction(
                        lambda unit: RuntimeOwnership.check_data_contract(
                            unit, "stable", schema, writes, readable
                        )
                    )
        for package in ("Upper", "slash/name", "a" * 65, "a..b"):
            with self.assertRaises(ValueError):
                await self.executor.run_internal_transaction(
                    lambda unit: RuntimeOwnership.check_data_contract(
                        unit, package, 1, 1, (1,)
                    )
                )
