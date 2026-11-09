"""Test-only joins of real retained adapter cleanup after bounded Host observers."""

import asyncio

from ygl_test_subject.services.core_runtime import CoreRuntimeCleanupPending


async def terminate_and_drain(runtime, *, timeout=10):
    """Require actual quiet, retaining the same cleanup future across timeout."""
    try:
        await runtime.terminate()
    except CoreRuntimeCleanupPending:
        cleanup = getattr(runtime, "_retained_cleanup", None)
        if cleanup is None:
            raise
        await asyncio.wait_for(asyncio.shield(cleanup), timeout)
        if runtime._retained_cleanup is not cleanup:
            raise AssertionError("cleanup owner changed while joining")
    if any(
        getattr(runtime, field, None) is not None
        for field in ("_core", "_http_transport", "_tool_publisher")
    ):
        raise AssertionError("runtime cleanup did not release actual owners")
