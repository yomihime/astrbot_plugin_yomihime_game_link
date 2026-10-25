"""Anonymous, declared redirect chains use the existing HTTP policy boundary."""

from __future__ import annotations

import asyncio
import unittest

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.infrastructure.http import SourceHttpService

from yomihime_game_link_sdk.declarations import SourceDeclaration
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.services import HttpRequest, HttpResponse


class _Chain:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.after_call = None

    async def request(self, request):
        self.calls.append(request)
        if self.after_call:
            await self.after_call(len(self.calls))
        return self.responses.pop(0)


def _source(**changes):
    values = dict(
        source_id="public",
        host="public.example.test",
        max_redirects=2,
        redirect_path_prefixes=("/profiles/", "/id/"),
    )
    values.update(changes)
    return validate_contract(SourceDeclaration(**values))


class RedirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_declared_chain_and_service_owned_final_path(self):
        chain = _Chain(
            HttpResponse(302, {"Location": "/id/example/?l=english"}, b""),
            HttpResponse(200, {}, b"public", "https://forged.test/fake"),
        )
        service = SourceHttpService((_source(),), chain)
        response = await service.fetch(HttpRequest("public", "/profiles/123/"))
        self.assertEqual(response.final_url, "https://public.example.test/id/example/")
        self.assertEqual(chain.calls[1].query, (("l", "english"),))
        self.assertEqual(response.body, b"public")
        self.assertLess(chain.calls[1].timeout_seconds, chain.calls[0].timeout_seconds)

    async def test_default_and_post_redirects_are_rejected_without_replay(self):
        for declaration, method in (
            (_source(max_redirects=0), "GET"),
            (_source(), "POST"),
        ):
            chain = _Chain(HttpResponse(302, {"Location": "/id/example/"}, b""))
            with self.assertRaises(SourceHttpError) as caught:
                await SourceHttpService((declaration,), chain).fetch(
                    HttpRequest("public", "/profiles/123/", method)
                )
            self.assertEqual(caught.exception.code, "redirect_disallowed")
            self.assertEqual(len(chain.calls), 1)

    async def test_unsafe_locations_fail_before_second_network_attempt(self):
        for location in (
            "http://public.example.test/id/a/",
            "https://evil.test/id/a/",
            "https://public.example.test.evil.test/id/a/",
            "https://public.example.test:444/id/a/",
            "//public.example.test/id/a/",
            "https://user:secret@public.example.test/id/a/",
            "/id/a/#",
            "/id/a/../b/",
            "/id/%2e%2e/",
            "/id/%252e%252e/",
            "/id/%2fa/",
            "/id\\a/",
            "/identity/a/",
            "/login/",
            "/id/a/?empty=",
        ):
            with self.subTest(location=location):
                chain = _Chain(HttpResponse(302, {"Location": location}, b""))
                with self.assertRaises(SourceHttpError) as caught:
                    await SourceHttpService((_source(),), chain).fetch(
                        HttpRequest("public", "/profiles/123/")
                    )
                self.assertEqual(caught.exception.code, "redirect_disallowed")
                self.assertEqual(len(chain.calls), 1)

    async def test_exact_https_host_explicit_443_is_allowed(self):
        chain = _Chain(
            HttpResponse(
                307, {"Location": "https://public.example.test:443/id/a/"}, b""
            ),
            HttpResponse(200, {}, b"ok"),
        )
        response = await SourceHttpService((_source(),), chain).fetch(
            HttpRequest("public", "/profiles/123/")
        )
        self.assertEqual(response.final_url, "https://public.example.test/id/a/")

    async def test_loop_missing_location_and_hop_limit_rejected(self):
        for responses, attempts in (
            ((HttpResponse(302, {"Location": "/profiles/123/"}, b""),), 1),
            ((HttpResponse(302, {}, b""),), 1),
            ((HttpResponse(304, {"Location": "/id/a/"}, b""),), 1),
            (
                (
                    HttpResponse(302, {"Location": "/id/a/"}, b""),
                    HttpResponse(302, {"Location": "/id/b/"}, b""),
                    HttpResponse(302, {"Location": "/id/c/"}, b""),
                ),
                3,
            ),
        ):
            chain = _Chain(*responses)
            with self.assertRaises(SourceHttpError) as caught:
                await SourceHttpService((_source(),), chain).fetch(
                    HttpRequest("public", "/profiles/123/")
                )
            self.assertEqual(caught.exception.code, "redirect_disallowed")
            self.assertEqual(len(chain.calls), attempts)

    async def test_each_hop_reserves_source_quota(self):
        chain = _Chain(HttpResponse(302, {"Location": "/id/a/"}, b""))
        with self.assertRaises(SourceHttpError) as caught:
            await SourceHttpService((_source(requests_per_minute=1),), chain).fetch(
                HttpRequest("public", "/profiles/123/")
            )
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(len(chain.calls), 1)

    async def test_final_429_keeps_retry_after_hint(self):
        chain = _Chain(
            HttpResponse(302, {"Location": "/id/a/"}, b""),
            HttpResponse(429, {"Retry-After": "12"}, b""),
        )
        with self.assertRaises(SourceHttpError) as caught:
            await SourceHttpService((_source(),), chain).fetch(
                HttpRequest("public", "/profiles/123/")
            )
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.retry_after_seconds, 12)

    async def test_revoked_authority_after_response_prevents_followup(self):
        active = True

        async def check():
            if not active:
                raise SourceHttpError("request_rejected")

        async def revoke(_):
            nonlocal active
            await asyncio.sleep(0)
            active = False

        chain = _Chain(HttpResponse(302, {"Location": "/id/a/"}, b""))
        chain.after_call = revoke
        with self.assertRaises(SourceHttpError) as caught:
            await SourceHttpService((_source(),), chain).fetch(
                HttpRequest("public", "/profiles/123/"), _check_authority=check
            )
        self.assertEqual(caught.exception.code, "request_rejected")
        self.assertEqual(len(chain.calls), 1)

    async def test_chain_uses_one_deadline_and_cancellation_releases_slots(self):
        chain = _Chain(
            HttpResponse(302, {"Location": "/id/a/"}, b""), HttpResponse(200, {}, b"ok")
        )

        async def delay(_):
            await asyncio.sleep(0.03)

        chain.after_call = delay
        service = SourceHttpService(
            (_source(timeout_seconds=0.05),), chain, max_concurrency=1
        )
        with self.assertRaises(SourceHttpError) as caught:
            await service.fetch(HttpRequest("public", "/profiles/123/"))
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(service._global_semaphore._value, 1)

        gate = asyncio.Event()
        started = asyncio.Event()

        async def block(index):
            if index == 2:
                started.set()
                await gate.wait()

        chain = _Chain(
            HttpResponse(302, {"Location": "/id/a/"}, b""), HttpResponse(200, {}, b"ok")
        )
        chain.after_call = block
        service = SourceHttpService((_source(),), chain, max_concurrency=1)
        task = asyncio.create_task(
            service.fetch(HttpRequest("public", "/profiles/123/"))
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(service._global_semaphore._value, 1)

    async def test_each_response_and_redirected_request_obeys_size_limits(self):
        for responses, kwargs, code in (
            (
                (HttpResponse(302, {"Location": "/id/a/"}, b"12345"),),
                {"max_response_bytes": 4},
                "response_too_large",
            ),
            (
                (
                    HttpResponse(302, {"Location": "/id/a/"}, b""),
                    HttpResponse(200, {}, b"12345"),
                ),
                {"max_response_bytes": 4},
                "response_too_large",
            ),
            (
                (HttpResponse(302, {"Location": "/id/" + "a" * 20}, b""),),
                {"max_path_length": 20},
                "redirect_disallowed",
            ),
        ):
            chain = _Chain(*responses)
            with self.assertRaises(SourceHttpError) as caught:
                await SourceHttpService((_source(),), chain, **kwargs).fetch(
                    HttpRequest("public", "/profiles/123/")
                )
            self.assertEqual(caught.exception.code, code)

    async def test_credentialed_source_cannot_opt_in_and_credentials_still_rejected(
        self,
    ):
        with self.assertRaises(ValueError):
            _source(credential_ref="credential_private")
        for header in ("Authorization", "Cookie", "X-Auth-Token"):
            chain = _Chain()
            with self.assertRaises(SourceHttpError):
                await SourceHttpService((_source(),), chain).fetch(
                    HttpRequest("public", "/profiles/123/", headers={header: "secret"})
                )
            self.assertEqual(chain.calls, [])

    def test_declaration_and_response_defaults_and_invalid_limits(self):
        self.assertEqual(
            SourceDeclaration("public", "public.example.test").max_redirects, 0
        )
        self.assertIsNone(validate_contract(HttpResponse(200, {}, b"ok")).final_url)
        for changes in (
            {"max_redirects": True},
            {"max_redirects": 3},
            {"redirect_path_prefixes": ()},
            {"redirect_path_prefixes": ("/id/../",)},
        ):
            with self.assertRaises(ValueError):
                _source(**changes)
        for url in (
            "https://user:secret@public.example.test/id/a/",
            "https://public.example.test/id/a/?key=secret",
        ):
            with self.assertRaises(ValueError):
                validate_contract(HttpResponse(200, {}, b"ok", url))
