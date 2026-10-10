"""Core-owned translation at module service boundaries.

Classify only closed attributes; raise a fresh public failure after leaving the
raw exception handler, so neither cause nor context retains dependency data.
"""

from functools import wraps
from inspect import iscoroutinefunction

from yomihime_game_link_sdk.errors import (
    AccessDenied,
    InvalidInvocation,
    OperationTimeout,
    ParameterError,
    RevisionConflict,
    ServiceUnavailable,
    SourceHttpError,
    UniqueConstraintViolation,
)

from .task_scope import ScopeClosedError, ScopeDeadlineExceeded, ScopeStaleError


def public_failure(error, phase="service"):
    if phase == "resource" and isinstance(error, FileNotFoundError):
        return FileNotFoundError("resource is unavailable")
    if isinstance(error, (OperationTimeout, TimeoutError, ScopeDeadlineExceeded)):
        return OperationTimeout()
    if (
        isinstance(error, InvalidInvocation)
        and getattr(error, "code", None) == "invocation_expired"
    ):
        return OperationTimeout()
    if isinstance(error, InvalidInvocation):
        return InvalidInvocation()
    if isinstance(error, ParameterError):
        return ParameterError("parameters are invalid")
    if isinstance(error, ServiceUnavailable):
        return ServiceUnavailable()
    if isinstance(error, RevisionConflict):
        try:
            return RevisionConflict(
                error.resource, error.expected_revision, error.actual_revision
            )
        except Exception:
            return ServiceUnavailable()
    if isinstance(error, UniqueConstraintViolation):
        try:
            return UniqueConstraintViolation(error.resource)
        except Exception:
            return ServiceUnavailable()
    if isinstance(error, SourceHttpError):
        status = error.status_code
        return SourceHttpError(
            error.code,
            status_code=status
            if type(status) is int and 100 <= status <= 599
            else None,
        )
    try:
        code = getattr(error, "code", None)
    except Exception:
        code = None
    if type(code) is not str:
        code = None
    if code in {"invocation_expired", "operation_timeout"}:
        return OperationTimeout()
    if code in {
        "invalid_invocation",
        "invalid_admission_lease",
        "admission_denied",
        "invocation_binding_unavailable",
    } or isinstance(error, (ScopeClosedError, ScopeStaleError)):
        return InvalidInvocation()
    if code in {"grant_expired", "grant_revoked", "source_revoked", "origin_denied"}:
        return AccessDenied(code)
    if phase == "proof" and code in {"capability_unavailable", "module_unavailable"}:
        return InvalidInvocation()
    if code in {
        "grant_unavailable",
        "principal_unavailable",
        "admission_unavailable",
        "cache_unavailable",
        "authorization_unavailable",
        "capability_unavailable",
        "module_unavailable",
    }:
        return ServiceUnavailable()
    if isinstance(error, AccessDenied):
        return AccessDenied(code if code in AccessDenied._CODES else "access_denied")
    if isinstance(error, PermissionError):
        return AccessDenied()
    if phase == "proof":
        return InvalidInvocation()
    if phase == "parameters" and isinstance(
        error, (TypeError, ValueError, AttributeError)
    ):
        return ParameterError("parameters are invalid")
    return ServiceUnavailable()


def public_boundary(phase="service"):
    def decorate(function):
        if iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                try:
                    return await function(*args, **kwargs)
                except Exception as error:
                    if phase == "invocation" and not isinstance(
                        error,
                        (
                            PermissionError,
                            TimeoutError,
                            ScopeDeadlineExceeded,
                            ScopeClosedError,
                            ScopeStaleError,
                        ),
                    ):
                        raise
                    failure = public_failure(error, phase)
                raise failure

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception as error:
                if phase == "invocation" and not isinstance(
                    error,
                    (
                        PermissionError,
                        TimeoutError,
                        ScopeDeadlineExceeded,
                        ScopeClosedError,
                        ScopeStaleError,
                    ),
                ):
                    raise
                failure = public_failure(error, phase)
            raise failure

        return synchronous

    return decorate


@public_boundary("parameters")
def parameters(check, *args, **kwargs):
    return check(*args, **kwargs)


@public_boundary("proof")
def proof(check, *args, **kwargs):
    return check(*args, **kwargs)
