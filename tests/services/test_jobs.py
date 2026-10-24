"""Real SQLite queue, production binder/issuer/Admission and owner boundaries."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ygl_test_subject.core.context_issuer import InvalidInvocation
from ygl_test_subject.core.contracts.services import (
    ConfigFieldUpdate,
    PersistedConfigPatch,
)
from ygl_test_subject.core.contracts.storage import SecretTarget
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.core.ports_jobs import JobError
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
from ygl_test_subject.services.jobs import JobsService, _now
from ygl_test_subject.services.source_credentials import (
    CredentialInjection,
    SourceCredentialPolicy,
)

from tests.fixtures import b03_runtime as fixtures
from tests.integration.test_managed_resources import png
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import (
    ConfigField,
    ConfigUpdateMode,
    PackageManifest,
    SourceDeclaration,
)
from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    ParameterError,
    SourceHttpError,
)
from yomihime_game_link_sdk.errors import (
    RevisionConflict as PublicRevisionConflict,
)
from yomihime_game_link_sdk.jobs import JobDescriptor, JobRequest, JobStatus
from yomihime_game_link_sdk.services import (
    ConfigTarget,
    HttpRequest,
    HttpResponse,
    ModuleHandlers,
)
from yomihime_game_link_sdk.storage import CacheVisibility
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION

SCHEMA = {
    "type": "object",
    "properties": {"count": {"type": "integer", "minimum": 1}},
    "required": ["count"],
    "additionalProperties": False,
}


class Handler:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.block = False
        self.fail = False
        self.document = False
        self.invocations = []
        self.control = None
        self.service = None
        self.on_bound = None
        self.bound_error = None
        self.published = asyncio.Event()
        self.pause_after_publish = False

    async def run(self, invocation, parameters, control):
        self.invocations.append(invocation)
        self.control = control
        bound = await self.service.bind_services(invocation)
        if self.on_bound is not None:
            try:
                await self.on_bound(invocation, bound)
            except Exception as error:
                self.bound_error = error
                raise
        await bound.cache.put(
            "selection", {"owner": invocation.actor_id}, ttl_seconds=60
        )
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            await control.update({"phase": "blocked", "reason": "synthetic_reason"})
            raise ValueError("not copied to public error")
        if self.document:
            for batch in range(parameters["count"]):
                await control.publish_document(
                    DisplayDocument("Job", "plain", (TextBlock("Bounded"),)),
                    metadata={"batch_index": batch},
                )
        else:
            for start in range(0, parameters["count"], 100):
                await control.publish(
                    tuple(
                        {"item": value}
                        for value in range(start, min(start + 100, parameters["count"]))
                    )
                )
                await control.update(
                    {
                        "processed": min(start + 100, parameters["count"]),
                        "total": parameters["count"],
                    }
                )
        self.published.set()
        if self.pause_after_publish:
            await self.release.wait()
        return {"count": parameters["count"]}


class CancellationBarrierHandler(Handler):
    """Hold old cancellation cleanup while later real runs bind/publish."""

    def __init__(self):
        super().__init__()
        self.cancelled = asyncio.Event()
        self.restarted = asyncio.Event()
        self.other_entered = asyncio.Event()
        self.new_release = asyncio.Event()
        self.other_release = asyncio.Event()
        self.live = 0
        self.maximum_live = 0

    async def run(self, invocation, parameters, control):
        self.invocations.append(invocation)
        generation = len(self.invocations)
        bound = await self.service.bind_services(invocation)
        await bound.cache.put("race-generation", {"value": generation}, ttl_seconds=60)
        self.live += 1
        self.maximum_live = max(self.maximum_live, self.live)
        try:
            if generation == 1:
                self.control = control
                self.entered.set()
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        self.cancelled.set()
            else:
                if parameters["count"] == 1:
                    self.restarted.set()
                    await self.new_release.wait()
                else:
                    self.other_entered.set()
                    await self.other_release.wait()
                await control.publish(({"generation": generation},))
                self.published.set()
            return {"generation": generation}
        finally:
            self.live -= 1


def registry(handler, *, timeout=30, active=2, credential=False):
    caps = fixtures._Handler()
    alpha = replace(
        fixtures._manifest("alpha"),
        jobs=(JobDescriptor("wall", SCHEMA, timeout, active),),
    )
    if credential:
        alpha = replace(
            alpha,
            config_fields=alpha.config_fields
            + (
                ConfigField(
                    "credential_test",
                    sensitive=True,
                    group="synthetic",
                    description="Synthetic key",
                ),
            ),
            sources=alpha.sources
            + (
                SourceDeclaration("auth", "auth.example.test", authorization_only=True),
            ),
        )
    result = Registry()
    result.register_package(
        validate_contract(
            PackageManifest(
                "sample",
                "1.0.0",
                MODULE_ABI_VERSION,
                (alpha, fixtures._manifest("beta")),
                "tests",
                "MIT",
                "isolated job consumer",
            )
        ),
        {
            "alpha": ModuleHandlers(
                {"read": caps, "private.read": fixtures._Handler("private.read")},
                {"account-collector": fixtures._Collector()},
                {},
                jobs={"wall": handler},
            ),
            "beta": ModuleHandlers({"read": caps}, {}, {}),
        },
    )
    return result


class DurableJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.handler = Handler()
        self.runtime = await fixtures.build_runtime(
            Path(self.temp.name), registry=registry(self.handler)
        )
        self.jobs = self.make_service()
        self.runtime.services.set_jobs(self.jobs)
        self.views = []

    def make_service(self, *, renderer=None):
        runtime = self.runtime

        async def bind(invocation):
            return await runtime.services.for_module(invocation.module_id).scopes.bind(
                invocation
            )

        service = JobsService(
            database=runtime.database,
            registry=runtime.registry,
            issuer=runtime.issuer,
            lifecycle=runtime.lifecycle,
            identities=runtime.repositories.identities,
            principal_resolver=runtime.services._principal_resolver,
            identity_namespace="test-users",
            bind_services=bind,
            resource_repository=runtime.repositories.resources,
            render_document=renderer,
        )
        self.handler.service = service
        return service

    async def asyncTearDown(self):
        self.handler.release.set()
        await self.jobs.close()
        for view, lease in self.views:
            try:
                self.runtime.issuer.release(view)
            except InvalidInvocation:
                pass
            self.runtime.lifecycle.admission.release(lease)
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def web(self, user="alice", *, current=lambda: True, module="sample/alpha"):
        return await self.jobs.for_dashboard(
            subject=user, module_id=module, current=current
        )

    async def terminal(self, operations, job_id):
        async def wait():
            while True:
                status = await operations.status(job_id)
                if status.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                    return status
                await asyncio.sleep(0.01)

        return await asyncio.wait_for(wait(), 5)

    async def command(
        self, actor="alice", *, origin=InvocationOrigin.COMMAND, adapter="test-adapter"
    ):
        module = self.runtime.registry.snapshot().module("sample/alpha")
        invocation = self.runtime.issuer.issue(
            origin=origin,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id=actor,
            adapter_id=adapter,
            conversation_id="room",
            capability_id="read",
        )
        lease = self.runtime.lifecycle.admission.admit(invocation, "read")
        self.views.append((invocation, lease))
        bound = await self.runtime.services.for_module(module.module_id).scopes.bind(
            invocation
        )
        return invocation, bound

    async def reset_runtime(self, *, credential=False, timeout=30):
        await self.jobs.close()
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)
        self.runtime = await fixtures.build_runtime(
            Path(self.temp.name) / "replacement",
            registry=registry(self.handler, credential=credential, timeout=timeout),
        )
        self.jobs = self.make_service()
        self.runtime.services.set_jobs(self.jobs)

    async def test_real_worker_persists_1501_entries_in_bounded_pages_and_new_scope(
        self,
    ):
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1501}, "one"))
        await self.jobs.start()
        final = await self.terminal(ops, job.job_id)
        self.assertIs(final.status, JobStatus.SUCCEEDED, final)
        self.assertEqual(final.summary["count"], 1501)
        self.assertEqual(final.summary["result_entries"], 1501)
        entries, cursor = [], None
        while True:
            page = await ops.result(job.job_id, cursor=cursor, page_size=100)
            entries.extend(page.entries)
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual([entry["item"] for entry in entries], list(range(1501)))
        invocation = self.handler.invocations[0]
        self.assertIs(invocation.origin, InvocationOrigin.JOB)
        self.assertIsNone(invocation.adapter_id)
        self.assertIsNone(invocation.conversation_id)
        with self.assertRaises(InvalidInvocation):
            self.runtime.issuer.require(invocation)

    async def test_new_dashboard_session_same_owner_other_user_module_and_command_isolated(
        self,
    ):
        alice = await self.web()
        job = await alice.submit(JobRequest("wall", {"count": 1}))
        refreshed = await self.web()
        self.assertEqual((await refreshed.status(job.job_id)).job_id, job.job_id)
        for other in (await self.web("bob"), await self.web(module="sample/beta")):
            with self.assertRaises(JobError) as failure:
                await other.status(job.job_id)
            self.assertEqual(failure.exception.code, "job_not_found")
        module = self.runtime.registry.snapshot().module("sample/alpha")
        invocation = self.runtime.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id="alice",
            adapter_id="test-adapter",
            conversation_id="room",
            capability_id="read",
        )
        lease = self.runtime.lifecycle.admission.admit(invocation, "read")
        self.views.append((invocation, lease))
        command = await self.jobs.for_invocation(invocation)
        with self.assertRaises(JobError):
            await command.status(job.job_id)
        bound = await self.runtime.services.for_module(module.module_id).scopes.bind(
            invocation
        )
        commanded = await bound.jobs.submit(JobRequest("wall", {"count": 1}))
        with self.assertRaises(JobError):
            await refreshed.status(commanded.job_id)

    async def test_dedup_limits_conflicting_key_and_cancel_revision(self):
        ops = await self.web()
        first, same = await asyncio.gather(
            ops.submit(JobRequest("wall", {"count": 1}, "a")),
            ops.submit(JobRequest("wall", {"count": 1}, "b")),
        )
        self.assertEqual(first.job_id, same.job_id)
        with self.assertRaises(JobError):
            await ops.submit(JobRequest("wall", {"count": 2}, "a"))
        await ops.submit(JobRequest("wall", {"count": 2}))
        with self.assertRaises(JobError) as full:
            await ops.submit(JobRequest("wall", {"count": 3}))
        self.assertEqual(full.exception.code, "job_capacity_exceeded")
        with self.assertRaises(RevisionConflict):
            await ops.cancel(first.job_id, expected_revision=first.revision + 1)
        cancelled = await ops.cancel(first.job_id, expected_revision=first.revision)
        self.assertIs(cancelled.status, JobStatus.CANCELLED)

    async def test_cancel_revokes_old_control_retry_gets_fresh_run_and_clears_entries(
        self,
    ):
        self.handler.block = True
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 2}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.entered.wait(), 5)
        old = self.handler.control
        running = await ops.status(job.job_id)
        cancelled = await ops.cancel(job.job_id, expected_revision=running.revision)
        with self.assertRaises((JobError, PermissionError)):
            await old.publish(({"late": True},))
        self.handler.block = False
        self.handler.release.set()
        while self.jobs._tasks:
            await asyncio.sleep(0.01)
        retried = await ops.retry(job.job_id, expected_revision=cancelled.revision)
        self.assertIs(retried.status, JobStatus.QUEUED)
        done = await self.terminal(ops, job.job_id)
        if self.handler.bound_error is not None:
            raise self.handler.bound_error
        self.assertIs(done.status, JobStatus.SUCCEEDED, done)
        page = await ops.result(job.job_id)
        self.assertEqual(tuple(entry["item"] for entry in page.entries), (0, 1))
        self.assertEqual(
            len({view.invocation_id for view in self.handler.invocations}), 2
        )

    async def test_real_persisted_claim_reopens_as_interrupted_without_saving_invocation(
        self,
    ):
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 3}))
        row = await self.jobs.repository.get(
            ops._caller.owner_id, "sample/alpha", job.job_id
        )
        await self.jobs.repository.claim(
            row, _now(), self.jobs._caller_check(ops._caller)
        )
        # No worker/driver was started: close the actual DB without graceful
        # JobsService.close(), so the new service must recover the persisted
        # running row rather than observing a shutdown-rewritten status.
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)
        self.runtime = await fixtures.build_runtime(
            Path(self.temp.name), registry=registry(self.handler)
        )
        self.jobs = self.make_service()
        self.runtime.services.set_jobs(self.jobs)
        await self.jobs.start()
        ops = await self.web()
        recovered = await ops.status(job.job_id)
        self.assertIs(recovered.status, JobStatus.INTERRUPTED)
        self.assertEqual(recovered.error_code, "process_interrupted")
        await ops.retry(job.job_id, expected_revision=recovered.revision)
        self.assertIs(
            (await self.terminal(ops, job.job_id)).status, JobStatus.SUCCEEDED
        )

    async def test_immediate_retry_waits_for_old_handler_cleanup_then_new_run_publishes(
        self,
    ):
        self.handler = CancellationBarrierHandler()
        await self.reset_runtime()
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.entered.wait(), 5)
        old_task = self.jobs._tasks[job.job_id]
        old_run = self.jobs._runs[job.job_id]
        cancelled = await ops.cancel(
            job.job_id, expected_revision=(await ops.status(job.job_id)).revision
        )
        await asyncio.wait_for(self.handler.cancelled.wait(), 5)
        scanned = asyncio.Event()
        queued = self.jobs.repository.queued

        async def observe_queue(limit):
            rows = await queued(limit)
            if any(row["job_id"] == job.job_id for row in rows):
                scanned.set()
            return rows

        self.jobs.repository.queued = observe_queue
        try:
            retried = await ops.retry(job.job_id, expected_revision=cancelled.revision)
            await asyncio.wait_for(scanned.wait(), 5)
            self.assertIs((await ops.status(job.job_id)).status, JobStatus.QUEUED)
            self.assertFalse(self.handler.restarted.is_set())
            self.assertIs(self.jobs._tasks[job.job_id], old_task)
            self.assertIs(self.jobs._runs[job.job_id], old_run)
            self.handler.release.set()
            await asyncio.gather(old_task, return_exceptions=True)
            await asyncio.wait_for(self.handler.restarted.wait(), 5)
            new_task = self.jobs._tasks[job.job_id]
            self.assertIsNot(new_task, old_task)
            self.assertIsNot(self.jobs._runs[job.job_id], old_run)
            self.assertEqual(self.handler.maximum_live, 1)
            self.handler.new_release.set()
            final = await self.terminal(ops, retried.job_id)
            self.assertIs(final.status, JobStatus.SUCCEEDED, final)
            self.assertEqual((await ops.result(job.job_id)).entries[0]["generation"], 2)
            await asyncio.gather(new_task, return_exceptions=True)
            self.assertNotIn(job.job_id, self.jobs._tasks)
            self.assertNotIn(job.job_id, self.jobs._runs)
        finally:
            self.jobs.repository.queued = queued

    async def test_waiting_retry_does_not_starve_other_job_or_exceed_worker_limit(self):
        self.handler = CancellationBarrierHandler()
        await self.reset_runtime()
        ops = await self.web()
        first = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.entered.wait(), 5)
        cancelled = await ops.cancel(
            first.job_id, expected_revision=(await ops.status(first.job_id)).revision
        )
        await asyncio.wait_for(self.handler.cancelled.wait(), 5)
        await ops.retry(first.job_id, expected_revision=cancelled.revision)
        other = await ops.submit(JobRequest("wall", {"count": 2}))
        await asyncio.wait_for(self.handler.other_entered.wait(), 5)
        self.assertIs((await ops.status(first.job_id)).status, JobStatus.QUEUED)
        self.assertFalse(self.handler.restarted.is_set())
        self.assertEqual(self.handler.live, 2)
        self.assertEqual(len(self.jobs._tasks), 2)
        self.handler.release.set()
        await asyncio.wait_for(self.handler.restarted.wait(), 5)
        self.assertEqual(self.handler.maximum_live, 2)
        self.handler.new_release.set()
        self.handler.other_release.set()
        self.assertIs(
            (await self.terminal(ops, first.job_id)).status, JobStatus.SUCCEEDED
        )
        self.assertIs(
            (await self.terminal(ops, other.job_id)).status, JobStatus.SUCCEEDED
        )

    async def test_stop_accepting_revokes_and_cancels_before_any_drain_await(self):
        self.handler.block = True
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.entered.wait(), 5)
        active = self.jobs._runs[job.job_id]
        task = self.jobs._tasks[job.job_id]
        self.jobs.stop_accepting()
        self.assertTrue(active["revoked"])
        self.assertTrue(active["scope"].cancelled)
        self.assertGreater(task.cancelling(), 0)
        self.assertFalse(self.jobs._close_recovered)
        with self.assertRaises(JobError):
            self.jobs._caller_check(ops._caller)()
        cancellation_requests = task.cancelling()
        self.jobs.stop_accepting()
        self.assertEqual(task.cancelling(), cancellation_requests)
        with self.assertRaises(JobError):
            await self.handler.control.publish(({"late": True},))
        self.handler.release.set()
        self.assertTrue(await self.jobs.close())
        self.assertFalse(self.jobs._busy_jobs())

    async def test_close_retains_cancel_resistant_run_until_real_cleanup_drains(self):
        self.handler = CancellationBarrierHandler()
        await self.reset_runtime()
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.entered.wait(), 5)
        cancelled = await ops.cancel(
            job.job_id, expected_revision=(await ops.status(job.job_id)).revision
        )
        await asyncio.wait_for(self.handler.cancelled.wait(), 5)
        await ops.retry(job.job_id, expected_revision=cancelled.revision)
        released = asyncio.Event()
        original = self.handler.invocations[0]
        unsubscribe = self.runtime.issuer.add_release_observer(
            lambda view: released.set() if view is original else None
        )
        try:
            self.assertFalse(await self.jobs.close())
            self.assertIn(job.job_id, self.jobs._runs)
            self.assertFalse(self.handler.restarted.is_set())
            self.handler.release.set()
            await asyncio.wait_for(released.wait(), 5)
            self.assertTrue(await self.jobs.close())
            self.assertEqual(self.jobs._tasks, {})
            self.assertEqual(self.jobs._runs, {})
        finally:
            unsubscribe()

    async def test_retried_run_remains_owned_by_cancel_and_close(self):
        self.handler = CancellationBarrierHandler()
        await self.reset_runtime()
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.entered.wait(), 5)
        old_task = self.jobs._tasks[job.job_id]
        cancelled = await ops.cancel(
            job.job_id, expected_revision=(await ops.status(job.job_id)).revision
        )
        await asyncio.wait_for(self.handler.cancelled.wait(), 5)
        await ops.retry(job.job_id, expected_revision=cancelled.revision)
        self.handler.release.set()
        await asyncio.gather(old_task, return_exceptions=True)
        await asyncio.wait_for(self.handler.restarted.wait(), 5)
        new_task = self.jobs._tasks[job.job_id]
        new_view = self.handler.invocations[-1]
        self.assertIsNot(new_task, old_task)
        stopped = await ops.cancel(
            job.job_id, expected_revision=(await ops.status(job.job_id)).revision
        )
        self.assertIs(stopped.status, JobStatus.CANCELLED)
        await asyncio.gather(new_task, return_exceptions=True)
        with self.assertRaises(InvalidInvocation):
            self.runtime.issuer.require(new_view)
        self.assertIs((await ops.status(job.job_id)).status, JobStatus.CANCELLED)
        self.assertTrue(await self.jobs.close())
        self.assertEqual(self.jobs._tasks, {})
        self.assertEqual(self.jobs._runs, {})

    async def test_cancel_before_worker_entry_retains_claim_settlement_before_retry(
        self,
    ):
        self.handler.block = True
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        scope = self.runtime.lifecycle.scope("sample/alpha")
        create_task = scope.create_task
        finish = self.jobs.repository.finish
        queued = self.jobs.repository.queued
        settle_entered, settle_release, scanned = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        first = [True]

        def cancel_before_entry(work, *, name):
            task = create_task(work, name=name)
            if first[0]:
                first[0] = False
                task.cancel()
            return task

        async def hold_settlement(*args, **kwargs):
            if args[2] == "interrupted" and not settle_release.is_set():
                settle_entered.set()
                await settle_release.wait()
            return await finish(*args, **kwargs)

        async def observe_queue(limit):
            rows = await queued(limit)
            if any(row["job_id"] == job.job_id for row in rows):
                scanned.set()
            return rows

        scope.create_task = cancel_before_entry
        self.jobs.repository.finish = hold_settlement
        self.jobs.repository.queued = observe_queue
        try:
            await self.jobs.start()
            await asyncio.wait_for(settle_entered.wait(), 5)
            self.assertEqual(self.handler.invocations, [])
            cancelled = await ops.cancel(
                job.job_id, expected_revision=(await ops.status(job.job_id)).revision
            )
            scanned.clear()
            await ops.retry(job.job_id, expected_revision=cancelled.revision)
            await asyncio.wait_for(scanned.wait(), 5)
            self.assertIs((await ops.status(job.job_id)).status, JobStatus.QUEUED)
            self.assertEqual(self.handler.invocations, [])
            settle_release.set()
            await asyncio.wait_for(self.handler.entered.wait(), 5)
            self.handler.release.set()
            self.assertIs(
                (await self.terminal(ops, job.job_id)).status, JobStatus.SUCCEEDED
            )
            self.assertEqual(len(self.handler.invocations), 1)
        finally:
            settle_release.set()
            scope.create_task = create_task
            self.jobs.repository.finish = finish
            self.jobs.repository.queued = queued

    async def test_document_batches_have_no_total_eight_page_cap_and_owner_asset_reads(
        self,
    ):
        async def render(invocation, document):
            return (
                ("image/png", png(len(self.handler.invocations) + len(document.title))),
            )

        self.jobs.render_document = render
        self.handler.document = True
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 11}))
        await self.jobs.start()
        done = await self.terminal(ops, job.job_id)
        self.assertIs(done.status, JobStatus.SUCCEEDED, done)
        result = await ops.result(job.job_id)
        self.assertEqual(len(result.entries), 11)
        asset = result.entries[0]["resource"]["asset_id"]
        media_type, content = await ops.read_result_asset(job.job_id, asset)
        self.assertEqual(media_type, "image/png")
        self.assertTrue(content.startswith(b"\x89PNG"))
        with self.assertRaises(JobError):
            await (await self.web("bob")).read_result_asset(job.job_id, asset)
        with self.assertRaises(JobError):
            await ops.read_result_asset(job.job_id, "asset_absent")

    async def test_no_renderer_is_explicit_failure_progress_failure_and_request_revocation(
        self,
    ):
        self.handler.document = True
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        failed = await self.terminal(ops, job.job_id)
        self.assertIs(failed.status, JobStatus.FAILED)
        self.assertEqual(failed.error_code, "renderer_unavailable")
        self.handler.document = False
        self.handler.fail = True
        job = await ops.submit(JobRequest("wall", {"count": 2}))
        failed = await self.terminal(ops, job.job_id)
        self.assertEqual(failed.progress["reason"], "synthetic_reason")
        alive = [True]
        stale = await self.web(current=lambda: alive[0])
        alive[0] = False
        with self.assertRaises(JobError):
            await stale.status(job.job_id)

    async def test_retry_invalidates_old_cursor_and_published_asset_association(self):
        async def render(invocation, document):
            return (("image/png", png(len(self.handler.invocations))),)

        self.jobs.render_document = render
        self.handler.document = True
        self.handler.pause_after_publish = True
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 2}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.published.wait(), 5)
        page = await ops.result(job.job_id, page_size=1)
        self.assertIsNotNone(page.next_cursor)
        asset = page.entries[0]["resource"]["asset_id"]
        running = await ops.status(job.job_id)
        cancelled = await ops.cancel(job.job_id, expected_revision=running.revision)
        while self.jobs._tasks:
            await asyncio.sleep(0.01)
        self.handler.pause_after_publish = False
        self.handler.release.set()
        await ops.retry(job.job_id, expected_revision=cancelled.revision)
        await self.terminal(ops, job.job_id)
        with self.assertRaises(JobError) as stale:
            await ops.result(job.job_id, cursor=page.next_cursor)
        self.assertEqual(stale.exception.code, "invalid_job_cursor")
        with self.assertRaises(JobError):
            await ops.read_result_asset(job.job_id, asset)
        self.assertEqual(len((await ops.result(job.job_id)).entries), 2)

    async def test_public_job_operations_raise_only_public_parameter_access_and_cas_errors(
        self,
    ):
        _, bound = await self.command()
        with self.assertRaises(ParameterError):
            await bound.jobs.submit(JobRequest("wall", {"count": "invalid"}))
        with self.assertRaises(AccessDenied):
            await bound.jobs.status("absent")
        job = await bound.jobs.submit(JobRequest("wall", {"count": 1}))
        with self.assertRaises(PublicRevisionConflict):
            await bound.jobs.cancel(job.job_id, expected_revision=job.revision + 1)
        await bound.jobs.cancel(job.job_id, expected_revision=job.revision)

    async def test_plain_job_origin_cannot_be_forged(self):
        with self.assertRaises(InvalidInvocation):
            self.runtime.issuer.issue(
                origin=InvocationOrigin.JOB,
                module_id="sample/alpha",
                module_epoch=1,
                registry_revision=1,
                actor_id="alice",
            )

    async def test_accepted_job_outlives_dashboard_request_and_timeout_is_bounded(self):
        self.handler.block = True
        alive = [True]
        ops = await self.web(current=lambda: alive[0])
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        await asyncio.wait_for(self.handler.entered.wait(), 5)
        alive[0] = False
        with self.assertRaises(JobError):
            await ops.status(job.job_id)
        self.handler.release.set()
        self.assertIs(
            (await self.terminal(await self.web(), job.job_id)).status,
            JobStatus.SUCCEEDED,
        )
        self.handler.entered.clear()
        self.handler.release.clear()
        await self.reset_runtime(timeout=1)
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        terminal = await self.terminal(ops, job.job_id)
        self.assertIs(terminal.status, JobStatus.FAILED)
        self.assertEqual(terminal.error_code, "job_timeout")

    async def test_tool_real_binder_submits_queries_but_never_manages_or_accepts_forged_view(
        self,
    ):
        invocation, bound = await self.command(origin=InvocationOrigin.LLM_TOOL)
        job = await bound.jobs.submit(JobRequest("wall", {"count": 1}))
        self.assertEqual((await bound.jobs.status(job.job_id)).job_id, job.job_id)
        _, command = await self.command()
        self.assertEqual((await command.jobs.status(job.job_id)).job_id, job.job_id)
        with self.assertRaises(PermissionError):
            await bound.jobs.cancel(job.job_id, expected_revision=job.revision)
        with self.assertRaises(JobError):
            await self.jobs.for_invocation(replace(invocation))
        no_adapter, _ = await self.command(
            origin=InvocationOrigin.LLM_TOOL, adapter=None
        )
        with self.assertRaises(JobError):
            await self.jobs.for_invocation(no_adapter)

    async def test_command_job_output_is_readable_by_normal_same_user_resources_only(
        self,
    ):
        self.jobs.render_document = lambda invocation, document: self.rendered()
        self.handler.document = True
        _, bound = await self.command()
        job = await bound.jobs.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        done = await self.terminal(bound.jobs, job.job_id)
        self.assertIs(done.status, JobStatus.SUCCEEDED, done)
        page = await bound.jobs.result(job.job_id)
        asset = page.entries[0]["resource"]["asset_id"]
        self.assertEqual(
            await bound.resources.read(asset, visibility=CacheVisibility.USER), png(9)
        )
        _, bob = await self.command("bob")
        with self.assertRaises(FileNotFoundError):
            await bob.resources.read(asset, visibility=CacheVisibility.USER)
        with self.assertRaises(JobError):
            await (await self.web()).status(job.job_id)

    async def rendered(self):
        return (("image/png", png(9)),)

    async def test_job_real_http_config_and_approved_static_injection_without_binding_authority(
        self,
    ):
        await self.reset_runtime(credential=True)
        transport = AioHttpTransport()
        self.addAsyncCleanup(transport.close)
        calls = []
        token = "synthetic-job-wire-key"

        async def send(method, url, body, headers, timeout, maximum):
            calls.append((method, str(url), headers))
            return HttpResponse(
                200, {}, json.dumps({"echo": token, "ok": True}).encode()
            )

        transport._send = send
        factory = self.runtime.services
        factory._http_transport = transport
        factory._source_credential_policies = (
            SourceCredentialPolicy(
                "sample/alpha",
                "catalog",
                "credential_test",
                "example.test",
                ("/v1/items/*",),
                kind="static",
                injections=(
                    CredentialInjection("/v1/items/*", "GET", "header", "X-Test-Key"),
                ),
            ),
        )
        repositories = self.runtime.repositories
        target = ConfigTarget("host-config", "sample/alpha")
        current = await repositories.config.current(target)
        secret_target = SecretTarget("host-config", "sample/alpha", "credential_test")
        operation = "job-test-static"
        receipt = await repositories.secret_store.stage(
            json.dumps({"schema": 2, "token": token}).encode(),
            target=secret_target,
            operation_id=operation,
            expected_config_revision=current.revision,
        )
        claimed = await repositories.secret_store.claim_for_config(
            receipt,
            target=secret_target,
            operation_id=operation,
            expected_config_revision=current.revision,
            expected_ledger_revision=receipt.ledger_revision,
        )
        updated = await repositories.config.update(
            target,
            PersistedConfigPatch(
                current.revision,
                (
                    ConfigFieldUpdate("region", ConfigUpdateMode.REPLACE, "job-region"),
                    ConfigFieldUpdate(
                        "credential_test", ConfigUpdateMode.REPLACE, receipt=receipt
                    ),
                ),
                self.runtime.registry.snapshot()
                .module("sample/alpha")
                .manifest.config_fields,
                operation,
                target,
            ),
        )
        await repositories.secret_store.finalize_active(
            claimed, metadata_revision=updated.revision
        )

        async def exercise(invocation, bound):
            config = await factory.for_module(invocation.module_id).config.current()
            self.assertEqual(config.values["region"], "job-region")
            self.assertNotIn("credential_test", config.values)
            self.assertEqual(len(config.secret_metadata), 1)
            response = await bound.http.fetch(HttpRequest("catalog", "/v1/items/one"))
            self.assertNotIn(token.encode(), response.body)
            for request in (
                HttpRequest("absent", "/x"),
                HttpRequest("auth", "/x"),
                HttpRequest("catalog", "/v1/items/one", method="POST"),
            ):
                with self.assertRaises(SourceHttpError):
                    await bound.http.fetch(request)
            with self.assertRaises(PermissionError):
                await factory.for_module(
                    invocation.module_id
                ).identities.default_identity(invocation)
            with self.assertRaises(PermissionError):
                await factory.for_module(invocation.module_id).accounts.bindings(
                    invocation
                )
            with self.assertRaises(PermissionError):
                await bound.records.collection("profiles")
            with self.assertRaises(PermissionError):
                await bound.jobs.submit(JobRequest("wall", {"count": 1}))
            dependency = await bound.dependencies.invoke(invocation, "read", {})
            self.assertEqual(dependency.error.code.value, "unsupported")
            self.assertFalse(hasattr(bound, "send"))

        self.handler.on_bound = exercise
        ops = await self.web()
        job = await ops.submit(JobRequest("wall", {"count": 1}))
        await self.jobs.start()
        done = await self.terminal(ops, job.job_id)
        if self.handler.bound_error is not None:
            raise self.handler.bound_error
        self.assertIs(done.status, JobStatus.SUCCEEDED, done)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0:2], ("GET", "https://example.test/v1/items/one"))
        self.assertEqual(calls[0][2]["X-Test-Key"], token)
