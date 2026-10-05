"""Owned native grants for repository tests; no production bypass or fake key."""

from types import SimpleNamespace

from ygl_test_subject.services.admin_authorization import AdminAuthorizationService


async def native_grant(repository, operation):
    context = SimpleNamespace(
        adapter_id="native-test", request_id="request", session_id="request"
    )
    service = AdminAuthorizationService(
        repository,
        context_validator=lambda _operation, _invocation, actual, _generation: actual
        is context,
    )
    return await service.authorize(operation, invocation=None, context=context)
