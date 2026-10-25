"""Core-private job run evidence. SDK values never mint these leases."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True, slots=True, eq=False)
class JobRunProof:
    job_id: str
    run_token: str
    handler_id: str
    owner_id: str
    declaration_digest: str
    current: Callable[[], bool] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, eq=False)
class JobRunLease:
    lease_id: str
    invocation_id: str
    job_id: str
    run_token: str
    module_id: str
    module_epoch: int
    registry_revision: int
    handler_id: str
    owner_id: str
    deadline_monotonic: float
    declaration_digest: str
    current: Callable[[], bool] = field(repr=False, compare=False)


class JobError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def declaration_digest(descriptor):
    def plain(item):
        if isinstance(item, Mapping):
            return {key: plain(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [plain(value) for value in item]
        return item

    raw = json.dumps(
        {
            "job_id": descriptor.job_id,
            "schema": plain(descriptor.parameter_schema),
            "timeout": descriptor.timeout_seconds,
            "active": descriptor.max_active_per_owner,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()
