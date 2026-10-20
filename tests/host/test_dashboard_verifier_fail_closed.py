"""Only the current synchronous Host verifier can authenticate management."""

import time
import unittest
from types import SimpleNamespace as N

from ygl_test_subject.adapters.astrbot.web_admin import verified_dashboard_request
from ygl_test_subject.core.contracts.administration import AdminAuthorizationDenied


class DashboardVerifierTests(unittest.TestCase):
    def request(self, server):
        path = "/synthetic/admin/module-page-session"
        headers = {
            "authorization": ["Bearer aaa.bbb.ccc"],
            "origin": ["https://synthetic.invalid"],
        }
        raw = N(
            url=N(scheme="https", netloc="synthetic.invalid", path=path),
            method="POST",
            state=N(dashboard_g=N(username="synthetic")),
            app=N(state=N(dashboard_app_adapter=N(_dashboard_server=server))),
        )
        raw.app.state.dashboard_app_adapter._app = raw.app
        return N(
            _request=raw,
            path=path,
            method="POST",
            username="synthetic",
            headers=N(getlist=lambda name: headers.get(name, [])),
        )

    def test_sync_current_host_identity_is_required(self):
        class Host:
            def _validate_dashboard_token(self, token, path):
                return {"username": "synthetic", "exp": int(time.time()) + 100}, ""

        request = self.request(Host())
        self.assertEqual(
            verified_dashboard_request(request, request.path)[1], "synthetic"
        )
        request._request.state.dashboard_g.username = None
        with self.assertRaises(AdminAuthorizationDenied):
            verified_dashboard_request(request, request.path)

    def test_async_and_exception_and_missing_request_context_fail_closed(self):
        class AsyncHost:
            async def _validate_dashboard_token(self, *_):
                return {"username": "synthetic", "exp": int(time.time()) + 100}, ""

        class FailedHost:
            def _validate_dashboard_token(self, *_):
                raise RuntimeError("synthetic verifier unavailable")

        for host in (AsyncHost(), FailedHost()):
            request = self.request(host)
            with self.assertRaises(AdminAuthorizationDenied):
                verified_dashboard_request(request, request.path)
        request = self.request(object())
        del request._request
        with self.assertRaises(AdminAuthorizationDenied):
            verified_dashboard_request(request, request.path)
