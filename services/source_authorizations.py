"""Core-private, UI-driven authorization sessions for host-reviewed policies.

The host supplies trusted transport, current-owner/page/epoch guards and an
atomic credential writer. A module descriptor alone never grants authority.
No provider-specific domains, fields, status values or persistence live here.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import math
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from yomihime_game_link_sdk.services import HttpRequest, HttpResponse

_MAX_ACTIVE_SESSIONS = 128
_MAX_TERMINAL_SESSIONS = 128


class AuthorizationError(RuntimeError):
    """A stable code, with no transport response or credential material."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _name(value: object) -> str:
    if type(value) is not str or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_-]{0,63}", value
    ):
        raise ValueError("Invalid authorization policy identifier")
    return value


def _path(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not value or len(value) > 12:
        raise ValueError("Invalid authorization response path")
    result = tuple(value)
    if any(type(item) is not str or not item or len(item) > 128 for item in result):
        raise ValueError("Invalid authorization response path")
    return result


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _at(payload, path):
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise AuthorizationError("invalid_response")
        current = current[key]
    return current


@dataclass(frozen=True, slots=True)
class AuthorizationBinding:
    owner_id: str
    actor_id: str
    page_session_id: str
    runtime_id: str
    owner_epoch: int
    expected_revision: int

    def __post_init__(self):
        if any(
            type(value) is not str or not value or len(value) > 256
            for value in (
                self.owner_id,
                self.actor_id,
                self.page_session_id,
                self.runtime_id,
            )
        ):
            raise ValueError("Invalid authorization binding")
        if any(
            type(value) is not int or value < 0
            for value in (self.owner_epoch, self.expected_revision)
        ):
            raise ValueError("Invalid authorization binding revision")


@dataclass(frozen=True, slots=True)
class CaptureRule:
    slot: str
    path: tuple[str, ...]
    url_query: str | None = None
    pattern: str | None = None

    def __post_init__(self):
        _name(self.slot)
        object.__setattr__(self, "path", _path(self.path))
        if self.url_query is not None:
            _name(self.url_query)
        if self.pattern is not None:
            if type(self.pattern) is not str or len(self.pattern) > 256:
                raise ValueError("Invalid capture pattern")
            re.compile(self.pattern)

    def capture(self, payload) -> str:
        value = _at(payload, self.path)
        if self.url_query is not None:
            if type(value) is not str or len(value) > 8192:
                raise AuthorizationError("invalid_response")
            try:
                values = [
                    item
                    for key, item in parse_qsl(urlsplit(value).query)
                    if key == self.url_query
                ]
            except ValueError:
                raise AuthorizationError("invalid_response") from None
            if len(values) != 1:
                raise AuthorizationError("invalid_response")
            value = values[0]
        if (
            type(value) is not str
            or not value
            or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise AuthorizationError("invalid_response")
        if self.pattern is not None and re.fullmatch(self.pattern, value) is None:
            raise AuthorizationError("invalid_response")
        return value


@dataclass(frozen=True, slots=True)
class AuthorizationStep:
    source_id: str
    path: str
    method: str
    json_body: Mapping[str, object] = field(default_factory=dict, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict)
    secret_json_fields: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        _name(self.source_id)
        if (
            type(self.path) is not str
            or not self.path.startswith("/")
            or self.path.startswith("//")
            or len(self.path) > 2048
            or any(char in self.path for char in ("?", "#", "\\", "%"))
            or any(part in (".", "..") for part in self.path.split("/"))
            or any(ord(char) <= 32 or ord(char) == 127 for char in self.path)
        ):
            raise ValueError("Authorization step requires a fixed relative path")
        if self.method not in ("GET", "POST"):
            raise ValueError("Invalid authorization step method")
        if not isinstance(self.json_body, Mapping) or not isinstance(
            self.headers, Mapping
        ):
            raise ValueError("Invalid authorization request template")
        try:
            body = json.loads(json.dumps(dict(self.json_body), allow_nan=False))
        except (ValueError, TypeError):
            raise ValueError("Authorization JSON must contain plain data") from None
        if len(json.dumps(body).encode()) > 16384:
            raise ValueError("Authorization request template too large")
        headers = dict(self.headers)
        for key, value in headers.items():
            if (
                type(key) is not str
                or type(value) is not str
                or len(value) > 2048
                or any(char in key + value for char in ("\r", "\n", "\x00"))
            ):
                raise ValueError("Invalid authorization request headers")
        fields = dict(self.secret_json_fields)
        for key, slot in fields.items():
            _name(key)
            _name(slot)
            if key in body:
                raise ValueError(
                    "Secret injection cannot overwrite public template data"
                )
        object.__setattr__(self, "json_body", _freeze(body))
        object.__setattr__(self, "headers", MappingProxyType(headers))
        object.__setattr__(self, "secret_json_fields", MappingProxyType(fields))

    def request(self, slots: Mapping[str, str]) -> HttpRequest:
        body = _thaw(self.json_body)
        for key, slot in self.secret_json_fields.items():
            if slot not in slots:
                raise AuthorizationError("capture_unavailable")
            body[key] = slots[slot]
        return HttpRequest(
            self.source_id,
            self.path,
            self.method,
            body=json.dumps(body).encode("utf-8") if self.method == "POST" else None,
            headers=self.headers,
        )


@dataclass(frozen=True, slots=True)
class AuthorizationFlowPolicy:
    flow_id: str
    apply: AuthorizationStep
    poll: AuthorizationStep
    qr_url_path: tuple[str, ...]
    ticket_capture: CaptureRule
    state_path: tuple[str, ...]
    pending_values: tuple[object, ...]
    success_values: tuple[object, ...]
    expired_values: tuple[object, ...]
    success_captures: tuple[CaptureRule, ...]
    subject_slot: str
    poll_interval_seconds: float
    ttl_seconds: float
    max_polls: int

    def __post_init__(self):
        _name(self.flow_id)
        object.__setattr__(self, "qr_url_path", _path(self.qr_url_path))
        object.__setattr__(self, "state_path", _path(self.state_path))
        if not isinstance(self.apply, AuthorizationStep) or not isinstance(
            self.poll, AuthorizationStep
        ):
            raise ValueError("Invalid authorization steps")
        if (
            not isinstance(self.ticket_capture, CaptureRule)
            or self.apply.secret_json_fields
        ):
            raise ValueError("Invalid authorization ticket policy")
        if set(self.poll.secret_json_fields.values()) != {self.ticket_capture.slot}:
            raise ValueError(
                "Poll injection must use only the captured temporary ticket"
            )
        seen = set()
        for name in ("pending_values", "success_values", "expired_values"):
            values = tuple(getattr(self, name))
            if not values:
                raise ValueError("Authorization states must be explicit")
            for value in values:
                if type(value) not in (str, int, bool) or (type(value), value) in seen:
                    raise ValueError(
                        "Authorization states must be distinct scalar values"
                    )
                seen.add((type(value), value))
            object.__setattr__(self, name, values)
        captures = tuple(self.success_captures)
        if (
            not captures
            or any(not isinstance(item, CaptureRule) for item in captures)
            or len({item.slot for item in captures}) != len(captures)
            or self.subject_slot not in {item.slot for item in captures}
        ):
            raise ValueError("Invalid final credential capture policy")
        object.__setattr__(self, "success_captures", captures)
        for value in (self.poll_interval_seconds, self.ttl_seconds):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Authorization timing must be finite and positive")
        if self.ttl_seconds > 300:
            raise ValueError("Authorization lifetime exceeds the safety limit")
        if type(self.max_polls) is not int or not 1 <= self.max_polls <= 150:
            raise ValueError("Authorization polling must be bounded")

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, Any]):
        """Parse plain data AFTER the host has checked its source/policy pins."""
        data = dict(descriptor)
        data["apply"] = AuthorizationStep(**data["apply"])
        data["poll"] = AuthorizationStep(**data["poll"])
        data["ticket_capture"] = CaptureRule(**data["ticket_capture"])
        data["success_captures"] = tuple(
            CaptureRule(**item) for item in data["success_captures"]
        )
        return cls(**data)


@dataclass(frozen=True, slots=True)
class AuthorizationSessionView:
    session_id: str
    state: str
    expires_at: float
    next_poll_at: float | None
    polls: int
    authorized_subject: str | None = None


@dataclass(slots=True, repr=False)
class _Session:
    session_id: str
    binding: AuthorizationBinding
    policy: AuthorizationFlowPolicy
    expires_at: float
    next_poll_at: float
    polls: int = 0
    state: str = "waiting"
    slots: dict[str, str] = field(default_factory=dict, repr=False)
    qr_url: str | None = field(default=None, repr=False)
    subject: str | None = None
    committing: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    expiry_timer: asyncio.TimerHandle | None = field(default=None, repr=False)

    def clear(self, state: str):
        self.state = state
        self.slots.clear()
        self.qr_url = None
        if self.expiry_timer is not None:
            self.expiry_timer.cancel()
            self.expiry_timer = None


def _encode_qr(url: str) -> bytes:
    try:
        qrcode = importlib.import_module("qrcode")
    except ImportError:
        raise AuthorizationError("qr_encoder_unavailable") from None
    stream = io.BytesIO()
    qrcode.make(url).save(stream, format="PNG")
    return stream.getvalue()


class SourceAuthorizations:
    def __init__(
        self,
        *,
        policies: Mapping[str, AuthorizationFlowPolicy],
        request: Callable[[HttpRequest], Awaitable[HttpResponse]],
        guard: Callable[[AuthorizationBinding], Awaitable[None]],
        credential_update: Callable[
            [AuthorizationBinding, Mapping[str, str]], Awaitable[object]
        ],
        clock: Callable[[], float] = time.time,
        qr_encoder: Callable[[str], bytes] | None = None,
    ):
        if any(key != policy.flow_id for key, policy in policies.items()):
            raise ValueError("Authorization flow policy key mismatch")
        self._policies = MappingProxyType(dict(policies))
        self._request, self._guard = request, guard
        self._credential_update, self._clock = credential_update, clock
        self._qr_encoder = qr_encoder or _encode_qr
        self._sessions: dict[str, _Session] = {}
        self._terminal: OrderedDict[str, _Session] = OrderedDict()
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise AuthorizationError("authorization_closed")

    def _session(self, session_id, binding):
        self._check_open()
        session = self._sessions.get(session_id) or self._terminal.get(session_id)
        if session is None or session.binding != binding:
            raise AuthorizationError("session_unavailable")
        if session.state == "waiting" and self._clock() >= session.expires_at:
            session.clear("expired")
        return session

    def _archive(self, session):
        if session.state == "waiting":
            return
        self._sessions.pop(session.session_id, None)
        if self._closed:
            return
        self._terminal[session.session_id] = session
        self._terminal.move_to_end(session.session_id)
        while len(self._terminal) > _MAX_TERMINAL_SESSIONS:
            self._terminal.popitem(last=False)

    def _expire(self, session):
        if session.state == "waiting":
            session.clear("expired")
            self._archive(session)

    def _view(self, session):
        self._archive(session)
        return AuthorizationSessionView(
            session.session_id,
            session.state,
            session.expires_at,
            session.next_poll_at if session.state == "waiting" else None,
            session.polls,
            session.subject,
        )

    async def _current(self, session):
        self._check_open()
        try:
            await self._guard(session.binding)
        except BaseException:
            session.clear("revoked")
            self._archive(session)
            raise
        self._check_open()
        if self._clock() >= session.expires_at and session.state == "waiting":
            session.clear("expired")
        if session.state != "waiting":
            raise AuthorizationError("session_inactive")

    async def _fetch(self, request):
        try:
            response = await self._request(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise AuthorizationError("authorization_request_failed") from None
        if response.status_code != 200 or len(response.body) > 65536:
            raise AuthorizationError("invalid_response")
        try:
            payload = json.loads(response.body)
        except (ValueError, TypeError, UnicodeDecodeError):
            raise AuthorizationError("invalid_response") from None
        if not isinstance(payload, dict):
            raise AuthorizationError("invalid_response")
        return payload

    async def begin(self, flow_id: str, binding: AuthorizationBinding):
        self._check_open()
        await self._guard(binding)
        self._check_open()
        policy = self._policies.get(flow_id)
        if policy is None:
            raise AuthorizationError("flow_unavailable")
        for old in tuple(self._sessions.values()):
            if self._clock() >= old.expires_at:
                self._expire(old)
            if (
                old.policy.flow_id == flow_id
                and old.binding.owner_id == binding.owner_id
                and old.binding.page_session_id == binding.page_session_id
                and old.binding.runtime_id == binding.runtime_id
            ):
                if old.committing:
                    raise AuthorizationError("authorization_busy")
                old.clear("cancelled")
                self._archive(old)
        if len(self._sessions) >= _MAX_ACTIVE_SESSIONS:
            raise AuthorizationError("authorization_capacity_exceeded")
        now = self._clock()
        session = _Session(
            secrets.token_urlsafe(32),
            binding,
            policy,
            now + policy.ttl_seconds,
            now + policy.poll_interval_seconds,
        )
        self._sessions[session.session_id] = session
        # Only erase private memory at expiry; this timer never performs I/O.
        session.expiry_timer = asyncio.get_running_loop().call_later(
            policy.ttl_seconds,
            self._expire,
            session,
        )
        try:
            payload = await self._fetch(policy.apply.request({}))
            await self._current(session)
            url = _at(payload, policy.qr_url_path)
            if type(url) is not str or len(url) > 8192:
                raise AuthorizationError("invalid_response")
            try:
                parsed = urlsplit(url)
                valid = (
                    parsed.scheme == "https" and parsed.netloc and not parsed.username
                )
            except ValueError:
                valid = False
            if not valid:
                raise AuthorizationError("invalid_response")
            session.slots[policy.ticket_capture.slot] = policy.ticket_capture.capture(
                payload
            )
            session.qr_url = url
            return self._view(session)
        except BaseException:
            if session.state == "waiting":
                session.clear("error")
            self._archive(session)
            raise

    async def poll(self, session_id: str, binding: AuthorizationBinding):
        session = self._session(session_id, binding)
        try:
            await self._guard(binding)
        except BaseException:
            session.clear("revoked")
            self._archive(session)
            raise
        if session.state != "waiting":
            return self._view(session)
        async with session.lock:
            if session.state != "waiting":
                return self._view(session)
            try:
                await self._current(session)
                if self._clock() < session.next_poll_at:
                    return self._view(session)
                if session.polls >= session.policy.max_polls:
                    session.clear("expired")
                    return self._view(session)
                session.polls += 1
                session.next_poll_at = (
                    self._clock() + session.policy.poll_interval_seconds
                )
                payload = await self._fetch(session.policy.poll.request(session.slots))
                await self._current(session)
                state = _at(payload, session.policy.state_path)

                def matches(values):
                    return any(
                        type(state) is type(value) and state == value
                        for value in values
                    )

                if matches(session.policy.success_values):
                    captures = {
                        rule.slot: rule.capture(payload)
                        for rule in session.policy.success_captures
                    }
                    await self._current(session)
                    # Host callback MUST re-check its atomic CAS/current guard.
                    session.committing = True
                    try:
                        await self._credential_update(
                            binding, MappingProxyType(captures)
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        raise AuthorizationError("credential_update_failed") from None
                    finally:
                        session.committing = False
                    self._check_open()
                    if session.state != "waiting":
                        raise AuthorizationError("session_inactive")
                    session.subject = captures[session.policy.subject_slot]
                    session.clear("authorized")
                elif matches(session.policy.expired_values):
                    session.clear("expired")
                elif not matches(session.policy.pending_values):
                    session.clear("error")
                    raise AuthorizationError("unknown_authorization_state")
                elif session.polls >= session.policy.max_polls:
                    session.clear("expired")
                return self._view(session)
            except BaseException:
                if session.state == "waiting":
                    session.clear("error")
                self._archive(session)
                raise

    async def qr_png(self, session_id: str, binding: AuthorizationBinding) -> bytes:
        session = self._session(session_id, binding)
        await self._current(session)
        if session.qr_url is None:
            raise AuthorizationError("qr_unavailable")
        try:
            png = self._qr_encoder(session.qr_url)
        except AuthorizationError:
            raise
        except Exception:
            raise AuthorizationError("qr_encoder_unavailable") from None
        if type(png) is not bytes or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AuthorizationError("qr_encoder_unavailable")
        await self._current(session)
        return png

    async def cancel(self, session_id: str, binding: AuthorizationBinding):
        session = self._session(session_id, binding)
        await self._guard(binding)
        if session.committing:
            # A commit that already began linearizes before cancellation. Never
            # report "cancelled" after persisting a credential successfully.
            async with session.lock:
                pass
        if session.state == "waiting":
            session.clear("cancelled")
        return self._view(session)

    async def revoke(self, flow_id: str, binding: AuthorizationBinding) -> None:
        self._check_open()
        await self._guard(binding)
        self._check_open()
        if flow_id not in self._policies:
            raise AuthorizationError("flow_unavailable")
        for session in tuple(self._sessions.values()):
            if (
                session.policy.flow_id == flow_id
                and session.binding.owner_id == binding.owner_id
                and session.binding.runtime_id == binding.runtime_id
            ):
                session.clear("revoked")
                self._archive(session)
        try:
            await self._credential_update(binding, MappingProxyType({}))
        except asyncio.CancelledError:
            raise
        except Exception:
            raise AuthorizationError("credential_update_failed") from None

    async def close(self) -> None:
        self._closed = True
        for session in self._sessions.values():
            session.clear("closed")
        self._sessions.clear()
        self._terminal.clear()

    def invalidate(self, owner_id=None, page_session_id=None) -> None:
        """Erase matching pending tickets synchronously; never performs I/O.

        The host's transaction fence must also reject a concurrently committing
        write after its owner/page lineage has been invalidated.
        """
        for session in tuple(self._sessions.values()):
            if (owner_id is None or session.binding.owner_id == owner_id) and (
                page_session_id is None
                or session.binding.page_session_id == page_session_id
            ):
                if session.state == "waiting":
                    session.clear("cancelled")
                    self._archive(session)
