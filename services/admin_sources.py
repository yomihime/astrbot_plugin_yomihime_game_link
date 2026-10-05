"""Core-owned generic authority policies and request proofs; no Host imports."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from time import time
from types import MappingProxyType
from uuid import uuid4

from ..api.administration import AdminAuthorizationDenied, AdminOperation
from ..api.services import ConfigTarget


@dataclass(frozen=True, slots=True, eq=False)
class _RequestContext:
    adapter_id: str
    request_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class _Proof:
    context: _RequestContext
    subject: str
    request: object
    expiry: float
    epoch: int
    task: object
    live: object
    resources: object
    operations: frozenset


class TrustedAdminSource:
    """A registered server-only issuer. IDs describe proofs; identity owns them.

    Deployment registration supplies a maximum operation/field policy. Issuing
    a proof can only narrow it. Invalidation and transaction commit share one
    lock, so neither an ended request nor a closed source can commit late.
    """

    def __init__(self, source_id, resources, operations):
        if type(source_id) is not str or not source_id.strip():
            raise ValueError("authority identifier is required")
        policy = {}
        for target, fields in resources.items():
            ConfigTarget.validate(target)
            fields = frozenset(fields)
            if not fields or any(type(f) is not str or not f for f in fields):
                raise ValueError("authority requires exact fields")
            policy[target] = fields
        operations = frozenset(operations)
        if (
            not policy
            or not operations
            or any(type(o) is not AdminOperation for o in operations)
        ):
            raise ValueError("authority requires explicit policy")
        self.source_id = source_id
        self.resources = MappingProxyType(policy)
        self.operations = operations
        self._lock = threading.RLock()
        self._epoch = 1
        self._closed = False
        self._proofs = {}

    def issue(self, *, subject, request, expiry, operations, resources, live):
        operations = frozenset(operations)
        requested = {t: frozenset(fs) for t, fs in resources.items()}
        if (
            type(subject) is not str
            or not subject.strip()
            or request is None
            or not callable(live)
            or not operations
            or not operations <= self.operations
            or not requested
            or any(
                t not in self.resources or not fs or not fs <= self.resources[t]
                for t, fs in requested.items()
            )
            or type(expiry) not in (int, float)
            or not time() < expiry <= time() + 60
        ):
            raise AdminAuthorizationDenied
        task = asyncio.current_task()
        if task is None:
            raise AdminAuthorizationDenied
        with self._lock:
            if self._closed:
                raise AdminAuthorizationDenied
            context = _RequestContext(self.source_id, uuid4().hex, uuid4().hex)
            proof = _Proof(
                context,
                subject,
                request,
                float(expiry),
                self._epoch,
                task,
                live,
                MappingProxyType(requested),
                operations,
            )
            self._proofs[id(context)] = proof
            self._check(context, next(iter(operations)), requested)
            return context

    def _check(self, context, operation, resources):
        proof = self._proofs.get(id(context))
        try:
            valid = (
                not self._closed
                and proof is not None
                and proof.context is context
                and proof.epoch == self._epoch
                and time() < proof.expiry
                and not proof.task.done()
                and not proof.task.cancelling()
                and proof.live(proof.request) is True
                and operation in proof.operations
                and bool(resources)
                and all(
                    t in proof.resources and bool(fs) and set(fs) <= proof.resources[t]
                    for t, fs in resources.items()
                )
            )
        except Exception:
            valid = False
        if not valid:
            raise AdminAuthorizationDenied
        return proof

    def check(self, context, operation, resources):
        with self._lock:
            return self._check(context, operation, resources)

    @contextmanager
    def fence(self, context, operation, resources):
        with self._lock:
            self._check(context, operation, resources)
            yield

    def end(self, context):
        with self._lock:
            proof = self._proofs.get(id(context))
            if proof is not None and proof.context is context:
                self._proofs.pop(id(context))

    def close(self):
        with self._lock:
            self._closed = True
            self._epoch += 1
            self._proofs.clear()
