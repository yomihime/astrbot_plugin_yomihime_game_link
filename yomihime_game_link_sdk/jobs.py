"""Durable owner-bound work. Identifiers and DTOs never confer authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .contexts import InvocationView
from .display import DisplayDocument
from .storage import JsonObject


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class JobDescriptor:
    job_id: str
    parameter_schema: JsonObject
    timeout_seconds: int = 1800
    max_active_per_owner: int = 2


@dataclass(frozen=True, slots=True)
class JobRequest:
    handler_id: str
    parameters: JsonObject
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class JobView:
    job_id: str
    revision: int
    handler_id: str
    status: JobStatus
    progress: JsonObject = field(default_factory=dict)
    summary: JsonObject = field(default_factory=dict)
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class JobResultPage:
    job: JobView
    entries: tuple[JsonObject, ...]
    next_cursor: str | None = None


class JobControl(Protocol):
    @property
    def checkpoint(self) -> JsonObject: ...

    async def update(
        self, progress: JsonObject, *, checkpoint: JsonObject | None = None
    ) -> None: ...

    async def publish(self, entries: tuple[JsonObject, ...]) -> None: ...

    async def publish_document(
        self, document: DisplayDocument, *, metadata: JsonObject | None = None
    ) -> None:
        """Core renders within its budgets and persists owner-bound result assets."""
        ...


class JobHandler(Protocol):
    async def run(
        self, invocation: InvocationView, parameters: JsonObject, control: JobControl
    ) -> JsonObject: ...


class JobOperations(Protocol):
    async def submit(self, request: JobRequest) -> JobView: ...

    async def status(self, job_id: str) -> JobView: ...

    async def result(
        self, job_id: str, *, cursor: str | None = None, page_size: int = 20
    ) -> JobResultPage: ...

    async def cancel(self, job_id: str, *, expected_revision: int) -> JobView: ...

    async def retry(self, job_id: str, *, expected_revision: int) -> JobView: ...
