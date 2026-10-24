"""Durable owner work, with fresh Core-issued execution and no ambient sender."""

import asyncio
import base64
import json
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from time import monotonic
from weakref import WeakSet

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import DisplayDocument
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    CacheUnavailable,
    ParameterError,
    ServiceUnavailable,
    SourceHttpError,
)
from yomihime_game_link_sdk.jobs import JobRequest, JobResultPage
from yomihime_game_link_sdk.services import ResourceCacheClass
from yomihime_game_link_sdk.storage import CacheVisibility, OwnerScope

from ..core.contracts.validation import _validate
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports_jobs import JobError, JobRunProof, declaration_digest
from ..core.public_errors import public_boundary
from ..core.task_scope import ScopeDeadlineExceeded, TaskScope
from ..infrastructure.sqlite.repositories_jobs import (
    SQLiteJobRepository,
    json_object,
    object_value,
    view,
)
from .identity import TrustedIngressPrincipalProvisioner


def _now():
    return datetime.now(UTC).isoformat()


def _text(value, maximum=256):
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or any(ord(c) < 32 for c in value)
    ):
        raise JobError("invalid_job_request")
    return value


@dataclass(eq=False, slots=True, weakref_slot=True)
class _Caller:
    owner_id: str
    module_id: str
    current: object
    manage: bool = True


class _Check:
    def __init__(self, service, check, lease=None):
        self.service, self.check, self.lease = service, check, lease

    def __call__(self):
        if self.service._closed:
            raise JobError("job_service_closed")
        self.check()

    @contextmanager
    def fence(self):
        with self.service._fence_lock:
            self()
            if self.lease is None:
                yield
            else:
                with self.service.lifecycle.admission.commit_guard(self.lease):
                    yield


class _Operations:
    def __init__(self, service, caller):
        self._service, self._caller = service, caller

    async def submit(self, request):
        return await self._service.submit(self._caller, request)

    async def status(self, job_id):
        return await self._service.status(self._caller, job_id)

    async def result(self, job_id, *, cursor=None, page_size=20):
        return await self._service.result(
            self._caller, job_id, cursor=cursor, page_size=page_size
        )

    async def cancel(self, job_id, *, expected_revision):
        return await self._service.change(
            self._caller, job_id, expected_revision, retry=False
        )

    async def retry(self, job_id, *, expected_revision):
        return await self._service.change(
            self._caller, job_id, expected_revision, retry=True
        )

    async def read_result_asset(self, job_id, asset_id):
        """Host-private; deliberately absent from SDK JobOperations."""
        return await self._service.read_result_asset(self._caller, job_id, asset_id)


class UnavailableJobs:
    async def submit(self, request):
        raise JobError("job_service_unavailable")

    async def status(self, job_id):
        raise JobError("job_service_unavailable")

    async def result(self, job_id, *, cursor=None, page_size=20):
        raise JobError("job_service_unavailable")

    async def cancel(self, job_id, *, expected_revision):
        raise JobError("job_service_unavailable")

    async def retry(self, job_id, *, expected_revision):
        raise JobError("job_service_unavailable")


class InvocationJobs:
    """Lazy binding lets a factory be assembled before Core wires its worker."""

    def __init__(self, provider, invocation):
        self.provider, self.invocation = provider, invocation

    @public_boundary("service")
    async def _invoke(self, method, *args, **kwargs):
        try:
            service = self.provider()
            if service is None:
                raise JobError("job_service_unavailable")
            bound = await service.for_invocation(self.invocation)
            return await getattr(bound, method)(*args, **kwargs)
        except JobError as error:
            if error.code in {"job_access_denied", "job_not_found", "job_run_revoked"}:
                failure = AccessDenied()
            elif error.code.startswith("invalid_") or error.code in {
                "job_not_retryable",
                "job_request_conflict",
                "job_data_limit",
            }:
                failure = ParameterError("job parameters are invalid")
            else:
                failure = ServiceUnavailable()
        raise failure

    async def submit(self, request):
        return await self._invoke("submit", request)

    async def status(self, job_id):
        return await self._invoke("status", job_id)

    async def result(self, job_id, *, cursor=None, page_size=20):
        return await self._invoke("result", job_id, cursor=cursor, page_size=page_size)

    async def cancel(self, job_id, *, expected_revision):
        return await self._invoke("cancel", job_id, expected_revision=expected_revision)

    async def retry(self, job_id, *, expected_revision):
        return await self._invoke("retry", job_id, expected_revision=expected_revision)


class JobsService:
    def __init__(
        self,
        *,
        database,
        registry,
        issuer,
        lifecycle,
        identities,
        principal_resolver,
        identity_namespace,
        bind_services,
        resource_repository=None,
        render_document=None,
        max_running=2,
        max_queued=128,
        max_result_bytes=64 * 1024 * 1024,
        retention_seconds=7 * 86400,
    ):
        for bound in (max_running, max_queued, max_result_bytes, retention_seconds):
            if type(bound) is not int or bound < 1:
                raise ValueError("job limits must be positive integers")
        self.repository = SQLiteJobRepository(database)
        self.registry, self.issuer, self.lifecycle = registry, issuer, lifecycle
        self.principal_resolver = principal_resolver
        self.bind_services, self.render_document = bind_services, render_document
        self.resources = resource_repository
        self.max_running, self.max_queued = max_running, max_queued
        self.max_result_bytes, self.retention_seconds = (
            max_result_bytes,
            retention_seconds,
        )
        self._web_owners = TrustedIngressPrincipalProvisioner(
            identities, identity_namespace=identity_namespace + ".jobs.dashboard.v1"
        )
        self._callers = WeakSet()
        self._source = object()
        self._fence_lock = RLock()
        self._closed, self._started = False, False
        self._close_recovered = False
        self._wake = asyncio.Event()
        self._tasks, self._runs = {}, {}
        self._settlements = set()
        self._settling = {}
        self._driver = None
        issuer.register_job_source(self._source)

    def _module(self, module_id):
        try:
            module = self.registry.snapshot().module(module_id)
            self.lifecycle.guard(module_id, epoch=module.epoch)
            if not module.enabled or not self.registry.is_active(module):
                raise ValueError()
            return module
        except Exception:
            raise JobError("job_module_unavailable") from None

    def _descriptor(self, module_id, handler_id):
        module = self._module(module_id)
        descriptor = next(
            (item for item in module.manifest.jobs if item.job_id == handler_id), None
        )
        if descriptor is None or handler_id not in module.handlers.jobs:
            raise JobError("job_handler_unavailable")
        return descriptor

    def _caller_check(self, caller):
        def check():
            if caller not in self._callers or caller.current() is not True:
                raise JobError("job_access_denied")
            self._module(caller.module_id)

        return _Check(self, check)

    async def for_dashboard(self, *, subject, module_id, current):
        """Trusted Host assembly only: subject must be verified by Host first.

        current is a synchronous exact-request/page/lease predicate. It must be
        checked after each await and at transaction commit; never store JWTs.
        """
        _text(subject, 1024)
        if not callable(current) or current() is not True or self._closed:
            raise JobError("job_access_denied")
        self._module(module_id)
        encoded = "u-" + base64.urlsafe_b64encode(subject.encode()).decode().rstrip("=")
        principal = await self._web_owners.ensure(encoded)
        if current() is not True:
            raise JobError("job_access_denied")
        caller = _Caller(principal.principal_id, module_id, current)
        self._callers.add(caller)
        self._caller_check(caller)()
        return _Operations(self, caller)

    async def for_invocation(self, invocation):
        def current():
            try:
                view = self.issuer.require_adapter(invocation)
                if view.origin not in (
                    InvocationOrigin.COMMAND,
                    InvocationOrigin.LLM_TOOL,
                ):
                    return False
                self.lifecycle.admission.check(self.issuer.lease_for(view))
                return True
            except Exception:
                return False

        if not current():
            raise JobError("job_access_denied")
        original = await self.principal_resolver.principal_id(invocation)
        if not current():
            raise JobError("job_access_denied")
        caller = _Caller(
            original,
            invocation.module_id,
            current,
            invocation.origin is InvocationOrigin.COMMAND,
        )
        self._callers.add(caller)
        self._caller_check(caller)()
        return _Operations(self, caller)

    async def submit(self, caller, request):
        check = self._caller_check(caller)
        check()
        await self.repository.expire(_now())
        check()
        if type(request) is not JobRequest:
            raise JobError("invalid_job_request")
        descriptor = self._descriptor(caller.module_id, _text(request.handler_id))
        if request.idempotency_key is not None:
            _text(request.idempotency_key)
        json_object(request.parameters)
        _validate(descriptor.parameter_schema, request.parameters, "parameters", 0)
        row = await self.repository.submit(
            owner=caller.owner_id,
            module=caller.module_id,
            handler=descriptor.job_id,
            declaration_digest=declaration_digest(descriptor),
            parameters=request.parameters,
            request_key=request.idempotency_key,
            now=_now(),
            expires_at=(
                datetime.now(UTC) + timedelta(seconds=self.retention_seconds)
            ).isoformat(),
            max_active=descriptor.max_active_per_owner,
            max_queued=self.max_queued,
            check=check,
        )
        check()
        self._wake.set()
        return view(row)

    async def _row(self, caller, job_id):
        check = self._caller_check(caller)
        check()
        row = await self.repository.get(
            caller.owner_id, caller.module_id, _text(job_id)
        )
        check()
        if row["expires_at"] <= _now():
            raise JobError("job_not_found")
        return row

    async def status(self, caller, job_id):
        return view(await self._row(caller, job_id))

    async def result(self, caller, job_id, *, cursor=None, page_size=20):
        initial = await self._row(caller, job_id)
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise JobError("invalid_job_cursor")
        offset = 0
        if cursor is not None:
            try:
                raw = base64.urlsafe_b64decode(
                    _text(cursor) + "=" * (-len(cursor) % 4)
                ).decode()
                bound_id, attempt, encoded_offset = raw.split(":")
                if (
                    bound_id != job_id
                    or attempt != str(initial["attempt"])
                    or not encoded_offset.isascii()
                    or not encoded_offset.isdigit()
                ):
                    raise ValueError()
                offset = int(encoded_offset)
                if offset > 2**53:
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise JobError("invalid_job_cursor") from None
        row, entries = await self.repository.results(
            caller.owner_id, caller.module_id, job_id, offset, page_size
        )
        self._caller_check(caller)()
        if row["attempt"] != initial["attempt"]:
            raise JobError("invalid_job_cursor")
        next_cursor = (
            base64.urlsafe_b64encode(
                f"{job_id}:{row['attempt']}:{offset + page_size}".encode()
            )
            .decode()
            .rstrip("=")
            if len(entries) > page_size
            else None
        )
        return JobResultPage(view(row), entries[:page_size], next_cursor)

    async def change(self, caller, job_id, expected_revision, *, retry):
        self._caller_check(caller)()
        if not caller.manage:
            raise JobError("job_access_denied")
        row = await self._row(caller, job_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise JobError("invalid_job_revision")
        descriptor = self._descriptor(caller.module_id, row["handler_id"])
        if retry and declaration_digest(descriptor) != row["declaration_digest"]:
            raise JobError("job_declaration_changed")
        updated = await self.repository.change(
            caller.owner_id,
            caller.module_id,
            job_id,
            expected_revision,
            retry=retry,
            now=_now(),
            max_active=descriptor.max_active_per_owner,
            max_queued=self.max_queued,
            check=self._caller_check(caller),
        )
        if not retry and updated["status"] == "cancelled":
            with self._fence_lock:
                active = self._runs.get(job_id)
                if active is not None:
                    active["revoked"] = True
                    if active["scope"] is not None:
                        active["scope"].cancel()
                task = self._tasks.get(job_id)
                if task is not None:
                    task.cancel()
        self._caller_check(caller)()
        self._wake.set()
        return view(updated)

    async def read_result_asset(self, caller, job_id, asset_id):
        await self._row(caller, job_id)
        _text(asset_id)

        async def linked():
            return await self.repository.database.executor.run_read(
                lambda unit: bool(
                    unit.execute(
                        "SELECT 1 FROM durable_job_results r JOIN durable_jobs j ON j.job_id=r.job_id "
                        "WHERE j.job_id=? AND j.owner_id=? AND j.module_id=? AND j.expires_at>? "
                        "AND json_extract(r.entry_json,'$.resource.asset_id')=? LIMIT 1",
                        (job_id, caller.owner_id, caller.module_id, _now(), asset_id),
                    ).fetchone()
                )
            )

        associated = await linked()
        self._caller_check(caller)()
        if not associated or self.resources is None:
            raise JobError("job_result_unavailable")
        scope = OwnerScope.user(caller.owner_id)
        owner = await self.resources.managed_owner(asset_id, scope)
        if owner != (caller.module_id, "output"):
            raise JobError("job_result_unavailable")
        manager = self.resources.managed_cache
        snapshot = await manager.snapshot(asset_id, scope)
        self._caller_check(caller)()
        content = await manager.read_snapshot(snapshot)
        self._caller_check(caller)()
        if not await linked():
            raise JobError("job_result_unavailable")
        self._caller_check(caller)()
        return snapshot.metadata.media_type, content

    def scope_for(self, invocation):
        proof = self.issuer.job_binding(invocation)
        active = self._runs.get(proof.job_id)
        if active is None or active["token"] != proof.run_token:
            raise JobError("job_run_revoked")
        return active["scope"]

    async def start(self):
        if self._closed:
            raise JobError("job_service_closed")
        if self._started:
            return
        await self.repository.recover(_now())
        await self.repository.expire(_now())
        self._started = True
        self._driver = asyncio.create_task(self._drive(), name="ygl:jobs-driver")
        self._wake.set()

    def notify_changed(self):
        self._wake.set()

    def _busy_jobs(self):
        # A cancelled supervisor can finish before its resistant handler.
        # Retain that run, and an unstarted claim's settlement, as worker slots.
        return self._tasks.keys() | self._runs.keys() | self._settling.keys()

    async def _drive(self):
        while not self._closed:
            await self._wake.wait()
            self._wake.clear()
            await self.repository.expire(_now())
            # At most len(busy) leading rows can be waiting retries. Looking
            # through max_running rows leaves every free slot a fair candidate.
            for candidate in await self.repository.queued(self.max_running):
                busy = self._busy_jobs()
                if self._closed or len(busy) >= self.max_running:
                    break
                if candidate["job_id"] in busy:
                    continue
                row = None
                try:
                    descriptor = self._descriptor(
                        candidate["module_id"], candidate["handler_id"]
                    )
                    if (
                        declaration_digest(descriptor)
                        != candidate["declaration_digest"]
                    ):
                        raise JobError("job_declaration_changed")
                    module = self._module(candidate["module_id"])
                    check = _Check(
                        self,
                        lambda: self.lifecycle.guard(
                            module.module_id, epoch=module.epoch
                        ),
                    )
                    row = await self.repository.claim(candidate, _now(), check)
                    if row is None:
                        continue
                    work = self.lifecycle.scope(module.module_id).create_task(
                        self._run(row, descriptor), name="job:" + row["job_id"]
                    )
                    self._tasks[row["job_id"]] = work
                    work.add_done_callback(
                        lambda task, record=row: self._done(record, task)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    code = (
                        error.code
                        if isinstance(error, JobError)
                        else "job_admission_failed"
                    )
                    if row is not None:
                        await self.repository.finish(
                            row["job_id"], row["run_token"], "failed", {}, code, _now()
                        )
                    else:
                        await self.repository.reject_queued(
                            candidate["job_id"], code, _now()
                        )

    def _done(self, row, task):
        job_id = row["job_id"]
        owned = self._tasks.get(job_id) is task
        if owned:
            self._tasks.pop(job_id)
        if not task.cancelled():
            task.exception()
        if owned and not row.get("_entered"):
            cleanup = asyncio.create_task(
                self.repository.finish(
                    job_id,
                    row["run_token"],
                    "interrupted",
                    {},
                    "job_interrupted",
                    _now(),
                ),
                name="ygl:job-unstarted",
            )
            self._settlements.add(cleanup)
            self._settling[job_id] = cleanup

            def settled(work):
                self._settlements.discard(work)
                if self._settling.get(job_id) is work:
                    self._settling.pop(job_id)
                if not work.cancelled():
                    work.exception()
                self._wake.set()

            cleanup.add_done_callback(settled)
        self._wake.set()

    async def _run(self, row, descriptor):
        row["_entered"] = True
        invocation, lease = None, None
        active = {"token": row["run_token"], "revoked": False, "scope": None}
        self._runs[row["job_id"]] = active
        deadline = monotonic() + descriptor.timeout_seconds

        def current():
            return (
                not self._closed
                and not active["revoked"]
                and self._runs.get(row["job_id"]) is active
                and monotonic() < deadline
            )

        try:
            module = self._module(row["module_id"])
            proof = JobRunProof(
                row["job_id"],
                row["run_token"],
                row["handler_id"],
                row["owner_id"],
                row["declaration_digest"],
                current,
            )
            invocation = self.issuer.issue_job(
                self._source,
                proof,
                module_id=row["module_id"],
                module_epoch=module.epoch,
                registry_revision=self.registry.snapshot().revision,
                deadline=deadline,
            )
            lease = self.lifecycle.admission.admit_job(invocation)
            check = _Check(self, lambda: self.lifecycle.admission.check(lease), lease)
            scope = TaskScope(
                module.module_id,
                module.epoch,
                deadline_monotonic=deadline,
                current_check=check,
            )
            active["scope"] = scope
            control = _Control(self, row, invocation, check)
            summary = await scope.await_result(
                module.handlers.jobs[row["handler_id"]].run(
                    invocation, object_value(row["parameters_json"]), control
                )
            )
            check()
            await self.repository.finish(
                row["job_id"],
                row["run_token"],
                "succeeded",
                summary,
                None,
                _now(),
                check=check,
            )
        except asyncio.CancelledError:
            await self.repository.finish(
                row["job_id"],
                row["run_token"],
                "interrupted",
                {},
                "job_interrupted",
                _now(),
            )
            raise
        except Exception as error:
            code = (
                error.code
                if isinstance(error, JobError)
                else "job_timeout"
                if isinstance(error, ScopeDeadlineExceeded)
                else error.code
                if isinstance(error, (CacheUnavailable, SourceHttpError))
                else "job_execution_failed"
            )
            await self.repository.finish(
                row["job_id"], row["run_token"], "failed", {}, code, _now()
            )
        finally:
            active["revoked"] = True
            try:
                if active["scope"] is not None:
                    await active["scope"].stop()
            finally:
                try:
                    if invocation is not None and self.resources is not None:
                        await self.resources.release_invocation_pins(
                            invocation.invocation_id
                        )
                finally:
                    if invocation is not None:
                        try:
                            self.issuer.release(invocation)
                        except Exception:
                            pass
                    if self._runs.get(row["job_id"]) is active:
                        self._runs.pop(row["job_id"])
                    self._wake.set()

    async def close(self):
        if (
            self._closed
            and self._close_recovered
            and not self._busy_jobs()
            and not self._settlements
        ):
            self.issuer.unregister_job_source(self._source)
            return True
        with self._fence_lock:
            self._closed = True
            for active in self._runs.values():
                active["revoked"] = True
                if active["scope"] is not None:
                    active["scope"].cancel()
        self._wake.set()
        if self._driver is not None:
            self._driver.cancel()
            await asyncio.gather(self._driver, return_exceptions=True)
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=TaskScope.DEFAULT_CLEANUP_TIMEOUT)
        await asyncio.sleep(0)
        if self._settlements:
            await asyncio.wait(
                tuple(self._settlements), timeout=TaskScope.DEFAULT_CLEANUP_TIMEOUT
            )
        await self.repository.recover(_now())
        self._close_recovered = True
        if not self._busy_jobs() and not self._settlements:
            self.issuer.unregister_job_source(self._source)
        return not self._busy_jobs() and not self._settlements


class _Control:
    def __init__(self, service, row, invocation, check):
        self.service, self.row, self.invocation, self.check = (
            service,
            row,
            invocation,
            check,
        )
        self._checkpoint = object_value(row["checkpoint_json"])

    @property
    def checkpoint(self):
        self.check()
        return self._checkpoint

    async def update(self, progress, *, checkpoint=None):
        self.check()
        await self.service.repository.update(
            self.row["job_id"],
            self.row["run_token"],
            progress,
            checkpoint,
            _now(),
            self.check,
        )
        self.check()
        if checkpoint is not None:
            self._checkpoint = object_value(json_object(checkpoint))

    async def publish(self, entries):
        self.check()
        if type(entries) is not tuple or not 1 <= len(entries) <= 100:
            raise JobError("job_publish_limit")
        canonical, references = [], []
        for entry in entries:
            entry = json.loads(json_object(entry, max_bytes=65536))
            if isinstance(entry.get("resource"), Mapping):
                resource = entry["resource"]
                if self.service.resources is None:
                    raise JobError("job_result_unavailable")
                owner = await self.service.resources.managed_owner(
                    resource.get("asset_id"), OwnerScope.user(self.row["owner_id"])
                )
                self.check()
                if owner != (self.row["module_id"], "output"):
                    raise JobError("job_result_scope_denied")
                snapshot = await self.service.resources.managed_cache.snapshot(
                    resource["asset_id"], OwnerScope.user(self.row["owner_id"])
                )
                self.check()
                if resource.get("media_type") != snapshot.metadata.media_type:
                    raise JobError("job_result_scope_denied")
                entry["resource"]["size_bytes"] = snapshot.metadata.size_bytes
                references.append((resource["asset_id"], snapshot.metadata.scope))
            canonical.append(entry)
        if references:
            active = self.service._runs[self.row["job_id"]]
            active["output_pin"] = await self.service.resources.pin_resources(
                tuple(references),
                module_id=self.row["module_id"],
                invocation_id=self.invocation.invocation_id,
                owner_key=self.row["owner_id"],
                now=datetime.now(UTC),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=max(1, self.invocation.deadline - monotonic())),
                pin_id=active.get("output_pin"),
                commit_guard=self.check.fence,
            )
            self.check()
        await self.service.repository.publish(
            self.row["job_id"],
            self.row["run_token"],
            tuple(canonical),
            _now(),
            self.check,
            self.service.max_result_bytes,
        )
        self.check()

    async def publish_document(self, document, *, metadata=None):
        self.check()
        if type(document) is not DisplayDocument:
            raise JobError("invalid_job_document")
        validate_contract(document)
        json_object({} if metadata is None else metadata, max_bytes=16384)
        if self.service.render_document is None:
            raise JobError("renderer_unavailable")
        rendered = await self.service.render_document(self.invocation, document)
        self.check()
        if type(rendered) is not tuple or not 1 <= len(rendered) <= 8:
            raise JobError("job_render_limit")
        if any(
            type(page) is not tuple
            or len(page) != 2
            or type(page[0]) is not str
            or type(page[1]) is not bytes
            for page in rendered
        ):
            raise JobError("job_render_limit")
        current = await self.service.repository.get(
            self.row["owner_id"], self.row["module_id"], self.row["job_id"]
        )
        self.check()
        if (
            current["result_bytes"] + sum(len(page[1]) for page in rendered)
            > self.service.max_result_bytes
        ):
            raise JobError("job_result_limit")
        bound = await self.service.bind_services(self.invocation)
        entries = []
        for media_type, content in rendered:
            self.check()
            reference = await bound.resources.store(
                media_type,
                content,
                visibility=CacheVisibility.USER,
                cache_class=ResourceCacheClass.OUTPUT,
            )
            entries.append(
                {
                    "resource": {
                        "asset_id": reference.asset_id,
                        "media_type": reference.media_type,
                    },
                    "metadata": {} if metadata is None else metadata,
                }
            )
        await self.publish(tuple(entries))
