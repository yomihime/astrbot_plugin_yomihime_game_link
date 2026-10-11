"""Public failures: stable identities and fixed safe messages."""

from __future__ import annotations

import re

_LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")


def _label(resource: str) -> str:
    if type(resource) is not str or not _LABEL.fullmatch(resource):
        raise ValueError("resource must be a bounded logical label")
    return resource


class RevisionConflict(RuntimeError):
    code = "revision_conflict"

    def __init__(self, resource: str, expected_revision: int, actual_revision: int):
        self.resource = _label(resource)
        if any(
            type(value) is not int or value < 0
            for value in (expected_revision, actual_revision)
        ):
            raise ValueError("revisions must be exact nonnegative integers")
        self.expected_revision, self.actual_revision = (
            expected_revision,
            actual_revision,
        )
        super().__init__("expected revision does not match current revision")


class UniqueConstraintViolation(RuntimeError):
    code = "unique_constraint"

    def __init__(self, resource: str):
        self.resource = _label(resource)
        super().__init__("unique constraint rejected")


class AccessDenied(PermissionError):
    _CODES = frozenset(
        {
            "access_denied",
            "invalid_invocation",
            "grant_expired",
            "grant_revoked",
            "source_revoked",
            "origin_denied",
        }
    )

    def __init__(self, code: str = "access_denied"):
        if type(code) is not str or code not in self._CODES:
            raise ValueError("unsupported permission failure code")
        self.code = code
        super().__init__("access is denied")


class InvalidInvocation(AccessDenied):
    code = "invalid_invocation"

    def __init__(self, _diagnostic: str | None = None):
        # Core may retain diagnostics separately; the public exception never does.
        super().__init__("invalid_invocation")


class ServiceUnavailable(RuntimeError):
    code = "service_unavailable"

    def __init__(self):
        super().__init__("service is unavailable")


class OperationTimeout(TimeoutError):
    code = "operation_timeout"

    def __init__(self):
        super().__init__("operation deadline was exceeded")


class ParameterError(ValueError):
    """Input does not satisfy the declared capability schema."""


class SourceHttpError(RuntimeError):
    """Stable, sanitized failure from the source HTTP boundary."""

    _MESSAGES = {
        "source_not_declared": "HTTP source is not declared",
        "request_rejected": "HTTP request is outside the declared source policy",
        "request_too_large": "HTTP request exceeds the source policy limit",
        "response_too_large": "HTTP response exceeds the source policy limit",
        "rate_limited": "HTTP source quota exceeded",
        "concurrency_limited": "HTTP source concurrency limit exceeded",
        "timeout": "HTTP source request timed out",
        "cancelled": "HTTP source request was cancelled",
        "credentials_unavailable": "HTTP source credentials are unavailable",
        "transport_failed": "HTTP source transport failed",
        "invalid_response": "HTTP source returned an invalid response",
        "upstream_error": "HTTP source returned an upstream error",
        "redirect_disallowed": "HTTP source redirect is not allowed",
    }

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        if code not in self._MESSAGES:
            code = "transport_failed"
        self.code = code
        self.status_code = status_code
        super().__init__(self._MESSAGES[code])
