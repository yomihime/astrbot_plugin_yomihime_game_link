"""Bounded JSON control plane owning asynchronous package operations.

Activation is supplied by Core composition. No wire caller can supply a factory,
policy, root, or executable object. Cancellation never drops an owned operation.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from uuid import uuid4

from ..extensions.hot_update_protocol import ProtocolError, parse_control
from .extension_discovery import DiscoveryError


@dataclass(slots=True)
class _Operation:
    instance: object = field(repr=False)
    request_id: str
    descriptor_digest: str
    operation_id: str
    reply: dict
    task: object = field(default=None, repr=False)
    lease: str | None = field(default=None, repr=False)
    activating: bool = True
    cancellation_requested: bool = False


class ExtensionControlEndpoint:
    """Queue discovery hints; derive all executable authority inside Core."""

    def __init__(
        self,
        discovery,
        *,
        core_epoch,
        activate,
        withdraw,
        fence,
        features=(),
        capacity=256,
        installation_guard=None,
    ):
        if type(core_epoch) is not int or core_epoch < 1:
            raise ValueError("invalid Core epoch")
        if type(capacity) is not int or not 1 <= capacity <= 65536:
            raise ValueError("invalid operation capacity")
        self.discovery = discovery
        self.core_epoch = core_epoch
        self.activate, self.withdraw, self.fence = activate, withdraw, fence
        self.features = tuple(features)
        self._installation_guard = installation_guard
        self.capacity = capacity
        self._operations = OrderedDict()
        self._leases = {}
        self._closed = False
        self._reconcile_task = None

    @staticmethod
    def _reply(state, **values):
        return {"protocol": "ygl.control/1", "ok": True, "state": state, **values}

    @staticmethod
    def _error(code, *, retryable=False):
        return {
            "protocol": "ygl.control/1",
            "ok": False,
            "state": "rejected",
            "error": {"code": code, "retryable": retryable, "details": {}},
        }

    async def request(self, caller, message):
        try:
            if type(message) is not dict:
                raise ProtocolError("invalid_request")
            # Serialize and parse, so no SDK DTO or arbitrary Python graph crosses.
            doc = parse_control(
                json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
            if self._installation_guard is not None and not self._installation_guard():
                return self._error("core_unavailable", retryable=True)
            if doc["op"] == "describe":
                return self._reply(
                    "parked" if self._closed else "ready",
                    core_epoch=self.core_epoch,
                    module_contract={"major": 2, "minor": 0},
                    features=list(self.features),
                )
            if doc["op"] == "disconnect":
                return self._disconnect(caller, doc["lease"])
            if doc["op"] == "status":
                return self._status(caller, doc)
            if self._closed:
                return self._error("core_unavailable", retryable=True)
            installation = self.discovery.for_instance(caller)
            # Check replay before descriptor revalidation: changed input for an
            # existing request UUID is always a conflict, never a second action.
            for operation in self._operations.values():
                if (
                    operation.instance is caller
                    and operation.request_id == doc["request_id"]
                ):
                    if operation.descriptor_digest != doc["descriptor_digest"]:
                        return self._error("request_conflict")
                    return self._reply(
                        "queued",
                        operation_id=operation.operation_id,
                        retry_after_ms=100,
                    )
            if installation.descriptor_digest != doc["descriptor_digest"]:
                return self._error("descriptor_changed", retryable=True)
            return self._queue(installation, doc["request_id"])
        except DiscoveryError as exc:
            return self._error(exc.code)
        except (ProtocolError, TypeError, ValueError, RecursionError):
            return self._error("invalid_request")
        except Exception:
            # Stable diagnostics only; no path, token or exception repr leaks.
            return self._error("control_failed", retryable=True)

    def _reserve(self):
        if len(self._operations) < self.capacity:
            return True
        for key, operation in tuple(self._operations.items()):
            if operation.task.done() and operation.lease is None:
                del self._operations[key]
                return True
        return False

    def _queue(self, installation, request_id):
        if not self._reserve():
            return self._error("busy", retryable=True)
        operation_id = str(uuid4())
        operation = _Operation(
            installation.instance,
            request_id,
            installation.descriptor_digest,
            operation_id,
            self._reply("queued", operation_id=operation_id),
        )
        self._operations[operation_id] = operation
        operation.task = asyncio.create_task(
            self._activate(operation, installation), name="ygl-package-connect"
        )
        return self._reply("queued", operation_id=operation_id, retry_after_ms=100)

    async def _activate(self, operation, installation):
        operation.reply = self._reply("preparing", operation_id=operation.operation_id)
        try:
            if self._closed or not self.discovery.current(installation):
                raise DiscoveryError("stale_instance")
            # Composition must recheck current() immediately before commit too.
            result = await self.activate(installation)
            if type(result) is not dict:
                raise TypeError("invalid activation result")
            # No publication authority comes from an input caller or reply.
            result = json.loads(json.dumps(result, allow_nan=False))
            if result.get("state") == "active":
                if self._closed or not self.discovery.current(installation):
                    self.fence(installation.instance)
                    await self.withdraw(installation.instance)
                    raise DiscoveryError("stale_instance")
                lease = next(
                    (
                        token
                        for token, existing in self._leases.items()
                        if existing.instance is operation.instance
                        and existing.descriptor_digest == operation.descriptor_digest
                        and existing.reply.get("state") == "active"
                    ),
                    None,
                )
                lease = lease or secrets.token_hex(32)
                previous = self._leases.get(lease)
                if previous is not None and previous is not operation:
                    previous.lease = None
                operation.lease = lease
                self._leases[lease] = operation
                result.update(lease=lease, core_epoch=self.core_epoch)
            operation.reply = self._reply(
                result.pop("state"), operation_id=operation.operation_id, **result
            )
        except DiscoveryError as exc:
            operation.reply = self._error(exc.code)
        except asyncio.CancelledError:
            if operation.reply.get("error", {}).get("code") != "prepare_timeout":
                operation.reply = self._error("operation_interrupted")
            raise
        except Exception as error:
            known = {
                "approval_required",
                "publisher_untrusted",
                "artifact_tampered",
                "release_downgrade",
                "environment_change_required",
                "package_already_owned",
                "data_incompatible",
                "prepare_side_effect_forbidden",
                "prepare_timeout",
                "active_package_budget_exceeded",
                "cleanup_pending",
                "revision_conflict",
                "stale_instance",
            }
            code = getattr(error, "code", None)
            if operation.reply.get("state") != "cleanup_pending":
                operation.reply = self._error(
                    code if code in known else "activation_failed"
                )
            elif code == "prepare_timeout":
                # Preparation has now actually drained; the earlier pending
                # diagnostic can become a completed, stable rejection.
                operation.reply = self._error("prepare_timeout")
        finally:
            operation.activating = False

    def _status(self, caller, doc):
        operation = self._operations.get(doc.get("operation_id"))
        if "lease" in doc:
            leased = self._leases.get(doc["lease"])
            if operation is not None and operation is not leased:
                return self._error("stale_lease")
            operation = leased
        if operation is None or operation.instance is not caller:
            return self._error("operation_unavailable")
        if operation.reply.get("state") == "active":
            try:
                self.discovery.for_instance(caller)
            except DiscoveryError:
                self.retire_instance(caller)
        return dict(operation.reply)

    def mark_instance_state(self, instance, state):
        for operation in tuple(self._operations.values()):
            if operation.instance is instance and operation.lease is None:
                operation.reply = self._reply(
                    state, operation_id=operation.operation_id
                )

    def mark_instance_error(self, instance, code):
        """Publish a stable diagnostic while the actual cleanup remains owned."""
        for operation in tuple(self._operations.values()):
            if operation.instance is instance and operation.lease is None:
                diagnostic = operation.reply.get("error", {}).get("code")
                error = self._error(
                    "prepare_timeout" if diagnostic == "prepare_timeout" else code,
                    retryable=True,
                )["error"]
                operation.reply = self._reply(
                    "cleanup_pending", operation_id=operation.operation_id, error=error
                )

    def retire_instance(self, instance, state="disabled"):
        """Retire every alias of exactly this instance, never a successor."""
        for lease, operation in tuple(self._leases.items()):
            if operation.instance is instance:
                del self._leases[lease]
        for operation in tuple(self._operations.values()):
            if operation.instance is instance:
                operation.lease = None
                operation.reply = self._reply(
                    state, operation_id=operation.operation_id
                )

    def _disconnect(self, caller, lease):
        operation = self._leases.get(lease)
        if operation is None or operation.instance is not caller:
            return self._reply("already_detached")
        # Synchronous fence before the first await, including Host terminate.
        self.fence(caller)
        self.discovery.tombstone(caller)
        self.retire_instance(caller, "cleanup_pending")
        operation.task = asyncio.create_task(
            self._withdraw(operation), name="ygl-package-disconnect"
        )
        return self._reply("queued", operation_id=operation.operation_id)

    async def _withdraw(self, operation):
        try:
            await self.withdraw(operation.instance)
            operation.reply = self._reply(
                "disabled", operation_id=operation.operation_id
            )
        except Exception:
            operation.reply = self._reply(
                "cleanup_pending",
                operation_id=operation.operation_id,
                reason="cleanup_failed",
            )

    def stop_accepting(self):
        self._closed = True
        for operation in tuple(self._leases.values()):
            self.fence(operation.instance)

    async def close(self):
        self.stop_accepting()
        # Snapshot preparatory owners before disconnect creates withdrawal
        # cleanup tasks. Each activation receives at most one cancel request.
        for operation in tuple(self._operations.values()):
            if (
                operation.activating
                and not operation.task.done()
                and not operation.cancellation_requested
            ):
                operation.cancellation_requested = True
                operation.task.cancel()
        for lease, operation in tuple(self._leases.items()):
            self._disconnect(operation.instance, lease)
        tasks = tuple(operation.task for operation in self._operations.values())
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
