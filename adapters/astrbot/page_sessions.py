"""Bounded opaque module-page grants, separate from public-query authority.

As with HostPublicWebValidator, raw Bearer tokens never enter the proof table.
A grant attests an authenticated Host session and closed page capability scope;
it does not attest which DOM issued a request or sandbox same-page JavaScript.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from secrets import token_bytes, token_urlsafe
from threading import RLock
from time import time

from ...core.contracts.administration import AdminAuthorizationDenied
from ...services.module_pages import ModulePageGrant


@dataclass(frozen=True, slots=True)
class _Session:
    subject: str
    bearer_digest: bytes
    binding: ModulePageGrant
    generation: int
    expiry: float


class HostModulePageSessions:
    """At most128 live grants, each lasting at most30 seconds.

    Rotation names only the current view's previous opaque proof; it never
    invalidates another view's parallel read/status/config grant.
    """

    def __init__(self, *, wall_clock=time):
        self._salt = token_bytes(32)
        self._clock = wall_clock
        self._sessions: dict[str, _Session] = {}
        self._lock = RLock()
        self._closed = False

    def bearer_digest(self, token: str) -> bytes:
        return hmac.digest(self._salt, token.encode("ascii"), "sha256")

    def _prune(self):
        now = self._clock()
        for handle, session in tuple(self._sessions.items()):
            if session.expiry <= now:
                del self._sessions[handle]

    def require(self, handle, *, subject, bearer_digest, binding, generation):
        with self._lock:
            self._prune()
            session = self._sessions.get(handle) if type(handle) is str else None
            if (
                self._closed
                or session is None
                or session.subject != subject
                or not hmac.compare_digest(session.bearer_digest, bearer_digest)
                or session.binding != binding
                or session.generation != generation
            ):
                raise AdminAuthorizationDenied
            return session

    def issue(
        self,
        *,
        subject,
        bearer_digest,
        binding,
        generation,
        expiry,
        previous_session=None,
    ):
        if type(binding) is not ModulePageGrant:
            raise AdminAuthorizationDenied
        with self._lock:
            self._prune()
            if self._closed or type(subject) is not str or not subject:
                raise AdminAuthorizationDenied
            if previous_session is not None:
                self.require(
                    previous_session,
                    subject=subject,
                    bearer_digest=bearer_digest,
                    binding=binding,
                    generation=generation,
                )
            if len(self._sessions) >= 128 and previous_session is None:
                raise AdminAuthorizationDenied
            expiry = min(float(expiry), self._clock() + 30.0)
            if expiry <= self._clock():
                raise AdminAuthorizationDenied
            if previous_session is not None:
                del self._sessions[previous_session]
            handle = token_urlsafe(32)
            self._sessions[handle] = _Session(
                subject, bearer_digest, binding, generation, expiry
            )
            return {"page_session": handle, "expires_at": expiry}

    def revoke(self, handle, *, subject, bearer_digest):
        """Closing an expired/unknown proof is idempotent, other sessions denied."""
        with self._lock:
            self._prune()
            session = self._sessions.get(handle) if type(handle) is str else None
            if session is None:
                return
            if session.subject != subject or not hmac.compare_digest(
                session.bearer_digest, bearer_digest
            ):
                raise AdminAuthorizationDenied
            del self._sessions[handle]

    def revoke_owner(self, owner):
        with self._lock:
            for handle, session in tuple(self._sessions.items()):
                if session.binding.owner == owner:
                    del self._sessions[handle]

    def close(self):
        with self._lock:
            self._closed = True
            self._sessions.clear()
