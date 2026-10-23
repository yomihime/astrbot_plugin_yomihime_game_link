"""A released real invocation cannot issue the next anonymous redirect hop."""

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.validation_boundary import validate_contract

import yomihime_game_link_sdk as ygl
from tests.fixtures import b03_runtime


class BoundHttpRedirectAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_while_first_hop_awaits_prevents_second_hop(self):
        original = b03_runtime._manifest

        def manifest(*args, **kwargs):
            return validate_contract(
                replace(
                    original(*args, **kwargs),
                    sources=(
                        ygl.SourceDeclaration(
                            "catalog",
                            "example.test",
                            max_redirects=1,
                            redirect_path_prefixes=("/public/",),
                        ),
                    ),
                )
            )

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(b03_runtime, "_manifest", manifest):
                runtime = await b03_runtime.build_runtime(Path(temporary))
            try:
                registered = runtime.registry.snapshot().module("sample/alpha")
                invocation = runtime.issuer.issue(
                    origin=ygl.InvocationOrigin.COMMAND,
                    module_id="sample/alpha",
                    module_epoch=registered.epoch,
                    registry_revision=runtime.registry.snapshot().revision,
                    actor_id="alice",
                    conversation_id="room",
                    adapter_id="test-adapter",
                    capability_id="read",
                )
                runtime.lifecycle.admission.admit(invocation, "read")
                bound = await runtime.services.for_module("sample/alpha").scopes.bind(
                    invocation
                )
                calls = []

                async def redirect(transport, request):
                    calls.append(request)
                    await asyncio.sleep(0)
                    runtime.issuer.release(invocation)
                    return ygl.HttpResponse(302, {"Location": "/public/profile/"}, b"")

                with patch.object(
                    b03_runtime.OfflineHttpTransport, "request", redirect
                ):
                    with self.assertRaises(
                        (ygl.InvalidInvocation, ygl.SourceHttpError)
                    ):
                        await bound.http.fetch(ygl.HttpRequest("catalog", "/profile/"))
                self.assertEqual(len(calls), 1)
            finally:
                await runtime.services.close_credentials()
                await runtime.database.executor.close(timeout=1)
