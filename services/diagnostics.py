"""Core-owned bounded diagnostics; modules cannot select identity or sink."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from types import MappingProxyType

from yomihime_game_link_sdk.errors import AccessDenied, OperationTimeout, ParameterError

from ..core.contracts.diagnostics import DiagnosticLimitExceeded, diagnostic_payload
from ..core.public_errors import public_boundary


@dataclass(frozen=True, slots=True)
class DiagnosticPolicy:
    event_ids: frozenset[str]
    max_events: int = 48
    max_bytes: int = 512 * 1024
    event_limits: object = None

    def __post_init__(self):
        object.__setattr__(self, "event_ids", frozenset(self.event_ids))
        limits = dict(self.event_limits or {})
        if not set(limits) <= self.event_ids or any(
            type(n) is not int or not 1 <= n <= self.max_events for n in limits.values()
        ):
            raise ValueError("invalid diagnostic event limit")
        object.__setattr__(self, "event_limits", MappingProxyType(limits))
        if (
            type(self.max_events) is not int
            or type(self.max_bytes) is not int
            or not 1 <= self.max_events <= 64
            or not 1 <= self.max_bytes <= 512 * 1024
        ):
            raise ValueError("invalid diagnostic policy budget")


class DiagnosticStore:
    """Eight live accounting slots and eight retained traces / 64 events / 512 KiB.

    Live quotas never reset when retained payloads are evicted. At capacity new
    invocations drop optional events until an exact issuer release frees a slot.
    """

    def __init__(self, sink=None):
        self._sink = sink
        self._traces = OrderedDict()
        self._bytes = 0
        self._retained_events = 0
        self._counts = {}
        self._closed = False
        self._issuer = None
        self._unsubscribe = None

    def bind_issuer(self, issuer):
        if self._closed:
            return
        if self._issuer is issuer:
            return
        if self._issuer is not None:
            raise ValueError("diagnostics issuer is already bound")
        self._issuer = issuer
        self._unsubscribe = issuer.add_release_observer(self.release)

    def release(self, invocation):
        counts = self._counts.get(invocation.invocation_id)
        if counts is not None and counts["invocation"] is invocation:
            self._counts.pop(invocation.invocation_id)

    def _evict_trace(self):
        _, old = self._traces.popitem(last=False)
        self._bytes -= old["bytes"]
        self._retained_events -= len(old["events"])

    def record(self, invocation, event_id, schema, payload, policy):
        try:
            safe, size = diagnostic_payload(schema, payload)
        except DiagnosticLimitExceeded:
            return
        except (ValueError, TypeError, OverflowError):
            raise ParameterError("diagnostic payload is invalid") from None
        if self._closed:
            return
        key = invocation.invocation_id
        counts = self._counts.get(key)
        # Reject before allocating metadata, including a single-event byte cap.
        if size > policy.max_bytes:
            return
        if counts is None:
            if len(self._counts) >= 8:
                return
        elif counts["invocation"] is not invocation:
            raise AccessDenied()
        if counts is not None and (
            counts["per_event"].get(event_id, 0)
            >= policy.event_limits.get(event_id, policy.max_events)
            or counts["events"] >= policy.max_events
            or counts["bytes"] + size > policy.max_bytes
        ):
            return
        event = {
            "owner": invocation.module_id,
            "epoch": invocation.module_epoch,
            "trace": key,
            "event_id": event_id,
            "payload": safe,
        }
        envelope_size = len(
            json.dumps(
                event, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            ).encode("ascii")
        )
        if envelope_size > 65536:
            return
        if counts is None:
            counts = {
                "invocation": invocation,
                "events": 0,
                "bytes": 0,
                "per_event": {},
            }
            self._counts[key] = counts
        counts["per_event"][event_id] = counts["per_event"].get(event_id, 0) + 1
        counts["events"] += 1
        counts["bytes"] += size
        # Retention is a rolling memory bound, not a runtime lifetime quota.
        while self._traces and (
            self._retained_events >= 64
            or self._bytes + size > 512 * 1024
            or key not in self._traces
            and len(self._traces) >= 8
        ):
            self._evict_trace()
        trace = self._traces.setdefault(key, {"bytes": 0, "events": []})
        trace["events"].append(event)
        trace["bytes"] += size
        self._bytes += size
        self._retained_events += 1
        if self._sink is not None:
            try:
                self._sink(event)
            except (AccessDenied, OperationTimeout):
                raise
            except Exception:
                pass

    def snapshot(self):
        return json.loads(
            json.dumps(
                [event for trace in self._traces.values() for event in trace["events"]]
            )
        )

    def close(self):
        self._closed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
        self._unsubscribe = None
        self._issuer = None
        self._traces.clear()
        self._bytes = self._retained_events = 0
        self._counts.clear()


class BoundDiagnostics:
    __slots__ = ("__invocation", "__declarations", "__policy", "__store", "__check")

    def __init__(self, invocation, declarations, policy, store, check):
        self.__invocation = invocation
        self.__declarations = MappingProxyType(
            {item.event_id: item.payload_schema for item in declarations}
        )
        self.__policy = policy
        self.__store = store
        self.__check = check

    @public_boundary("service")
    async def emit(self, event_id, payload):
        await self.__check()
        if (
            type(event_id) is not str
            or event_id not in self.__declarations
            or self.__policy is None
            or event_id not in self.__policy.event_ids
        ):
            raise AccessDenied()
        try:
            self.__store.record(
                self.__invocation,
                event_id,
                self.__declarations[event_id],
                payload,
                self.__policy,
            )
        except Exception:
            await self.__check()
            raise
        await self.__check()
