"""Fail-closed activation boundary for discovered extension declarations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .discovery import DiscoveredPackage, discover_packages


class CandidateState(StrEnum):
    INVALID = "invalid"
    DISABLED = "disabled"
    READY = "ready"


class ActivationUnavailable(RuntimeError):
    """Raised until an H-authorized Core registry/lifecycle backend is wired."""


class ExtensionBackend(Protocol):
    """Narrow structural view of the authorized Core extension runtime."""

    def scan(
        self, root: str | Path | None = None
    ) -> tuple[ExtensionCandidate, ...]: ...

    def candidate(self, package_id: str) -> ExtensionCandidate | None: ...

    def candidates(self) -> tuple[ExtensionCandidate, ...]: ...

    async def set_enabled(
        self,
        invocation: object | None,
        module_id: str,
        enabled: bool,
        *,
        expected_registry_revision: int,
        authorization: object | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class ExtensionCandidate:
    package: DiscoveredPackage
    state: CandidateState
    reason_code: str | None = None


class ExtensionLoader:
    """Preserve inert scanning or proxy the one authorized Core backend."""

    def __init__(self, backend: ExtensionBackend | None = None) -> None:
        self._backend = backend

    def scan(self, root: str | Path | None = None) -> tuple[ExtensionCandidate, ...]:
        if self._backend is not None:
            return self._backend.scan(root)
        return self._scan_inert(root)

    @staticmethod
    def _scan_inert(root: str | Path | None) -> tuple[ExtensionCandidate, ...]:
        candidates = []
        for package in discover_packages(root):
            if not package.valid:
                candidates.append(
                    ExtensionCandidate(
                        package, CandidateState.INVALID, "manifest_invalid"
                    )
                )
            else:
                candidates.append(
                    ExtensionCandidate(package, CandidateState.DISABLED, "not_enabled")
                )
        return tuple(candidates)

    def candidate(self, package_id: str) -> ExtensionCandidate | None:
        if self._backend is None:
            del package_id
            return None
        return self._backend.candidate(package_id)

    def candidates(self) -> tuple[ExtensionCandidate, ...]:
        if self._backend is None:
            return ()
        return self._backend.candidates()

    async def set_enabled(
        self,
        invocation: object | None,
        module_id: str,
        enabled: bool,
        *,
        expected_registry_revision: int,
        authorization: object | None = None,
    ) -> object:
        if self._backend is None:
            raise ActivationUnavailable(
                "authorized Core extension activation is unavailable"
            )
        return await self._backend.set_enabled(
            invocation,
            module_id,
            enabled,
            expected_registry_revision=expected_registry_revision,
            authorization=authorization,
        )

    async def enable(
        self, package_id: str, *, authorization: object | None = None
    ) -> None:
        """Fail closed until Core authorization and lifecycle adapters exist."""
        del package_id, authorization
        raise ActivationUnavailable(
            "authorized Core extension activation is unavailable"
        )
