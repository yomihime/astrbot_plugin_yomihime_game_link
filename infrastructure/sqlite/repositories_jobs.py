"""Transactional durable user work; no SDK object or task is serialized."""

import hashlib
import json
from collections.abc import Mapping
from contextlib import contextmanager
from uuid import uuid4

from yomihime_game_link_sdk.jobs import JobStatus, JobView

from ...core.contracts.storage import freeze_json
from ...core.ports import RevisionConflict
from ...core.ports_jobs import JobError


@contextmanager
def _fence(check):
    factory = getattr(check, "fence", None)
    if factory is not None:
        with factory():
            yield
    else:
        check()
        yield


def json_object(value, *, max_bytes=262144):
    def plain(item, depth=0):
        if depth > 16:
            raise JobError("job_data_limit")
        if isinstance(item, Mapping):
            return {key: plain(child, depth + 1) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [plain(child, depth + 1) for child in item]
        return item

    if not isinstance(value, Mapping):
        raise JobError("invalid_job_data")
    try:
        encoded = json.dumps(
            plain(freeze_json(value)),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        raise JobError("invalid_job_data") from None
    if len(encoded.encode("utf-8")) > max_bytes:
        raise JobError("job_data_limit")
    return encoded


def object_value(raw):
    return freeze_json(json.loads(raw))


def view(row):
    return JobView(
        row["job_id"],
        row["revision"],
        row["handler_id"],
        JobStatus(row["status"]),
        object_value(row["progress_json"]),
        object_value(row["summary_json"]),
        row["error_code"],
    )


class SQLiteJobRepository:
    def __init__(self, database):
        self.database = database

    async def recover(self, now):
        def write(unit):
            unit.execute(
                "UPDATE durable_jobs SET status='interrupted',revision=revision+1,"
                "run_token=NULL,error_code='process_interrupted',updated_at=? WHERE status='running'",
                (now,),
            )

        await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")

    @staticmethod
    def owned(unit, owner, module, job_id):
        row = unit.execute(
            "SELECT * FROM durable_jobs WHERE job_id=? AND owner_id=? AND module_id=?",
            (job_id, owner, module),
        ).fetchone()
        if row is None:
            raise JobError("job_not_found")
        return row

    async def get(self, owner, module, job_id):
        return await self.database.executor.run_read(
            lambda unit: dict(self.owned(unit, owner, module, job_id))
        )

    async def submit(
        self,
        *,
        owner,
        module,
        handler,
        declaration_digest,
        parameters,
        request_key,
        now,
        expires_at,
        max_active,
        max_queued,
        check,
    ):
        raw = json_object(parameters)
        digest = hashlib.sha256(
            (handler + ":" + declaration_digest + ":" + raw).encode()
        ).hexdigest()

        def write(unit):
            check()
            if request_key is not None:
                previous = unit.execute(
                    "SELECT job_id,input_digest FROM durable_job_requests "
                    "WHERE owner_id=? AND module_id=? AND request_key=?",
                    (owner, module, request_key),
                ).fetchone()
                if previous is not None:
                    if previous[1] != digest:
                        raise JobError("job_request_conflict")
                    return dict(self.owned(unit, owner, module, previous[0]))
            existing = unit.execute(
                "SELECT * FROM durable_jobs WHERE owner_id=? AND module_id=? "
                "AND handler_id=? AND input_digest=? AND status IN ('queued','running')",
                (owner, module, handler, digest),
            ).fetchone()
            if existing is None:
                count = unit.execute(
                    "SELECT COUNT(*) FROM durable_jobs WHERE owner_id=? AND module_id=? "
                    "AND handler_id=? AND status IN ('queued','running')",
                    (owner, module, handler),
                ).fetchone()[0]
                queued = unit.execute(
                    "SELECT COUNT(*) FROM durable_jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                if count >= max_active or queued >= max_queued:
                    raise JobError("job_capacity_exceeded")
                job_id = uuid4().hex
                unit.execute(
                    "INSERT INTO durable_jobs(job_id,module_id,owner_id,handler_id,declaration_digest,"
                    "parameters_json,input_digest,status,revision,created_at,updated_at,expires_at) "
                    "VALUES (?,?,?,?,?,?,?,'queued',1,?,?,?)",
                    (
                        job_id,
                        module,
                        owner,
                        handler,
                        declaration_digest,
                        raw,
                        digest,
                        now,
                        now,
                        expires_at,
                    ),
                )
            else:
                job_id = existing["job_id"]
            if request_key is not None:
                unit.execute(
                    "INSERT INTO durable_job_requests VALUES (?,?,?,?,?)",
                    (owner, module, request_key, digest, job_id),
                )
            unit.add_commit_guard(lambda: _fence(check))
            return dict(self.owned(unit, owner, module, job_id))

        return await self.database.executor.run_transaction(
            write, begin_mode="IMMEDIATE"
        )

    async def queued(self, limit):
        return await self.database.executor.run_read(
            lambda unit: tuple(
                dict(row)
                for row in unit.execute(
                    "SELECT * FROM durable_jobs WHERE status='queued' ORDER BY created_at,job_id LIMIT ?",
                    (limit,),
                ).fetchall()
            )
        )

    async def reject_queued(self, job_id, code, now):
        await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE durable_jobs SET status='failed',error_code=?,revision=revision+1,updated_at=? "
                "WHERE job_id=? AND status='queued'",
                (code, now, job_id),
            ).rowcount,
            begin_mode="IMMEDIATE",
        )

    async def claim(self, row, now, check):
        token = uuid4().hex

        def write(unit):
            check()
            count = unit.execute(
                "UPDATE durable_jobs SET status='running',revision=revision+1,run_token=?,"
                "updated_at=? WHERE job_id=? AND status='queued' AND revision=? AND expires_at>?",
                (token, now, row["job_id"], row["revision"], now),
            ).rowcount
            if count != 1:
                return None
            unit.add_commit_guard(lambda: _fence(check))
            return dict(
                unit.execute(
                    "SELECT * FROM durable_jobs WHERE job_id=?", (row["job_id"],)
                ).fetchone()
            )

        return await self.database.executor.run_transaction(
            write, begin_mode="IMMEDIATE"
        )

    @staticmethod
    def running(unit, job_id, token):
        row = unit.execute(
            "SELECT * FROM durable_jobs WHERE job_id=? AND status='running' AND run_token=?",
            (job_id, token),
        ).fetchone()
        if row is None:
            raise JobError("job_run_revoked")
        return row

    async def update(self, job_id, token, progress, checkpoint, now, check):
        progress_raw = json_object(progress, max_bytes=16384)
        checkpoint_raw = None if checkpoint is None else json_object(checkpoint)

        def write(unit):
            check()
            row = self.running(unit, job_id, token)
            count = unit.execute(
                "SELECT COUNT(*) FROM durable_job_results WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            effective_progress = json_object(
                {**json.loads(progress_raw), "result_entries": count}, max_bytes=16384
            )
            unit.execute(
                "UPDATE durable_jobs SET progress_json=?,checkpoint_json=?,revision=revision+1,updated_at=? "
                "WHERE job_id=? AND run_token=?",
                (
                    effective_progress,
                    row["checkpoint_json"]
                    if checkpoint_raw is None
                    else checkpoint_raw,
                    now,
                    job_id,
                    token,
                ),
            )
            unit.add_commit_guard(lambda: _fence(check))

        await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")

    async def publish(self, job_id, token, entries, now, check, max_result_bytes):
        if type(entries) is not tuple or not 1 <= len(entries) <= 100:
            raise JobError("job_publish_limit")
        encoded = tuple(json_object(entry, max_bytes=65536) for entry in entries)
        encoded_size = sum(len(raw.encode()) for raw in encoded)
        size = encoded_size + sum(
            entry.get("resource", {}).get("size_bytes", 0) for entry in entries
        )
        if encoded_size > 262144:
            raise JobError("job_publish_limit")

        def write(unit):
            check()
            row = self.running(unit, job_id, token)
            if row["result_bytes"] + size > max_result_bytes:
                raise JobError("job_result_limit")
            sequence = unit.execute(
                "SELECT COALESCE(MAX(sequence)+1,0) FROM durable_job_results WHERE job_id=?",
                (job_id,),
            ).fetchone()[0]
            for offset, raw in enumerate(encoded):
                unit.execute(
                    "INSERT INTO durable_job_results VALUES (?,?,?)",
                    (job_id, sequence + offset, raw),
                )
            progress = {
                **json.loads(row["progress_json"]),
                "result_entries": sequence + len(encoded),
            }
            unit.execute(
                "UPDATE durable_jobs SET result_bytes=result_bytes+?,progress_json=?,revision=revision+1,updated_at=? WHERE job_id=?",
                (size, json_object(progress, max_bytes=16384), now, job_id),
            )
            unit.add_commit_guard(lambda: _fence(check))

        await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")

    async def finish(self, job_id, token, status, summary, error, now, *, check=None):
        raw = json_object(summary, max_bytes=65536)

        def write(unit):
            if check is not None:
                check()
            count = unit.execute(
                "SELECT COUNT(*) FROM durable_job_results WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            effective_summary = json_object(
                {**json.loads(raw), "result_entries": count}, max_bytes=65536
            )
            unit.execute(
                "UPDATE durable_jobs SET status=?,summary_json=?,error_code=?,run_token=NULL,"
                "revision=revision+1,updated_at=? WHERE job_id=? AND status='running' AND run_token=?",
                (status, effective_summary, error, now, job_id, token),
            )
            if check is not None:
                unit.add_commit_guard(lambda: _fence(check))

        await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")

    async def change(
        self,
        owner,
        module,
        job_id,
        expected_revision,
        *,
        retry,
        now,
        max_active,
        max_queued,
        check,
    ):
        def write(unit):
            check()
            row = self.owned(unit, owner, module, job_id)
            if row["revision"] != expected_revision:
                raise RevisionConflict("job", expected_revision, row["revision"])
            if retry:
                if (
                    row["status"] not in ("interrupted", "failed", "cancelled")
                    or row["expires_at"] <= now
                ):
                    raise JobError("job_not_retryable")
                active = unit.execute(
                    "SELECT COUNT(*) FROM durable_jobs WHERE owner_id=? AND module_id=? "
                    "AND handler_id=? AND status IN ('queued','running')",
                    (owner, module, row["handler_id"]),
                ).fetchone()[0]
                total = unit.execute(
                    "SELECT COUNT(*) FROM durable_jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                if active >= max_active or total >= max_queued:
                    raise JobError("job_capacity_exceeded")
                duplicate = unit.execute(
                    "SELECT 1 FROM durable_jobs WHERE owner_id=? AND module_id=? AND handler_id=? "
                    "AND input_digest=? AND status IN ('queued','running')",
                    (owner, module, row["handler_id"], row["input_digest"]),
                ).fetchone()
                if duplicate:
                    raise JobError("job_request_conflict")
                unit.execute(
                    "DELETE FROM durable_job_results WHERE job_id=?", (job_id,)
                )
                unit.execute(
                    "UPDATE durable_jobs SET status='queued',progress_json='{}',checkpoint_json='{}',"
                    "summary_json='{}',error_code=NULL,result_bytes=0,run_token=NULL,revision=revision+1,attempt=attempt+1,updated_at=? "
                    "WHERE job_id=?",
                    (now, job_id),
                )
            elif row["status"] in ("queued", "running"):
                unit.execute(
                    "UPDATE durable_jobs SET status='cancelled',run_token=NULL,revision=revision+1,updated_at=? "
                    "WHERE job_id=?",
                    (now, job_id),
                )
            unit.add_commit_guard(lambda: _fence(check))
            return dict(self.owned(unit, owner, module, job_id))

        return await self.database.executor.run_transaction(
            write, begin_mode="IMMEDIATE"
        )

    async def results(self, owner, module, job_id, offset, limit):
        def read(unit):
            row = self.owned(unit, owner, module, job_id)
            entries = unit.execute(
                "SELECT entry_json FROM durable_job_results WHERE job_id=? AND sequence>=? "
                "ORDER BY sequence LIMIT ?",
                (job_id, offset, limit + 1),
            ).fetchall()
            return dict(row), tuple(object_value(entry[0]) for entry in entries)

        return await self.database.executor.run_read(read)

    async def expire(self, now):
        return await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "DELETE FROM durable_jobs WHERE expires_at<=? AND status<>'running'",
                (now,),
            ).rowcount,
            begin_mode="IMMEDIATE",
        )
