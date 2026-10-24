"""Validation of descriptive SDK jobs at Core receiving boundaries."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from yomihime_game_link_sdk.jobs import (
    JobStatus,
    JobView,
)

from .schema import freeze_input_schema
from .storage import freeze_json


def _identifier(value):
    if (
        type(value) is not str
        or not re.fullmatch(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*", value)
        or len(value) > 128
    ):
        raise ValueError("invalid job handler identifier")


def _object(value, limit=65536):
    if not isinstance(value, Mapping):
        raise TypeError("job data must be a JSON object")

    def plain(node):
        if isinstance(node, Mapping):
            return {key: plain(child) for key, child in node.items()}
        if isinstance(node, (tuple, list)):
            return [plain(child) for child in node]
        return node

    encoded = json.dumps(plain(value), allow_nan=False, ensure_ascii=False)
    if len(encoded.encode()) > limit:
        raise ValueError("job data exceeds budget")
    return freeze_json(value)


def check_JobDescriptor(self):
    _identifier(self.job_id)
    object.__setattr__(
        self, "parameter_schema", freeze_input_schema(self.parameter_schema)
    )
    if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 3600:
        raise ValueError("job deadline must be 1..3600 seconds")
    if (
        type(self.max_active_per_owner) is not int
        or not 1 <= self.max_active_per_owner <= 4
    ):
        raise ValueError("invalid job owner concurrency")


def check_JobRequest(self):
    _identifier(self.handler_id)
    object.__setattr__(self, "parameters", _object(self.parameters))
    if self.idempotency_key is not None and (
        type(self.idempotency_key) is not str
        or not 1 <= len(self.idempotency_key) <= 256
        or any(ord(c) < 32 for c in self.idempotency_key)
    ):
        raise ValueError("invalid job idempotency key")


def check_JobView(self):
    if type(self.job_id) is not str or not 1 <= len(self.job_id) <= 128:
        raise ValueError("invalid job identifier")
    _identifier(self.handler_id)
    if (
        type(self.revision) is not int
        or self.revision < 1
        or type(self.status) is not JobStatus
    ):
        raise ValueError("invalid job state")
    object.__setattr__(self, "progress", _object(self.progress, 16384))
    object.__setattr__(self, "summary", _object(self.summary, 16384))
    if self.error_code is not None:
        _identifier(self.error_code)


def check_JobResultPage(self):
    if (
        type(self.job) is not JobView
        or type(self.entries) is not tuple
        or len(self.entries) > 100
    ):
        raise ValueError("invalid bounded job result page")
    for entry in self.entries:
        _object(entry)
    if self.next_cursor is not None and (
        type(self.next_cursor) is not str or len(self.next_cursor) > 256
    ):
        raise ValueError("invalid job cursor")
