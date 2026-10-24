"""Known pre-dispatch refusal is distinct from ambiguous late send failures."""

import unittest
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ygl_test_subject.adapters.astrbot.command_bridge import CommandInvocation
from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
from ygl_test_subject.core.admission import AdmissionError, CapabilityUnavailable
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.services.core_runtime import (
    CoreRuntime,
    HostIngress,
    HostIngressUnavailable,
)

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.subscriptions import ConversationKind


class PreDispatchRejectionTests(unittest.IsolatedAsyncioTestCase):
    def ingress(self):
        return HostIngress(
            "adapter",
            "actor",
            "conversation",
            "route",
            ConversationKind.GROUP,
            object(),
        )

    def core(self, refusal):
        runtime = CoreRuntime.__new__(CoreRuntime)
        module = SimpleNamespace(enabled=True, epoch=1)
        runtime._host_flights = set()
        runtime._require_accepting_ingress = Mock()
        runtime._host_ingress = AsyncMock(return_value=object())
        runtime._active_module = Mock(return_value=(module, "read"))
        runtime._ingress_principal_provisioner = SimpleNamespace(ensure=AsyncMock())
        runtime.trusted_route_publisher = SimpleNamespace(publish=AsyncMock())
        runtime.registry = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(revision=1, module=lambda _: module)
        )
        runtime.lifecycle = SimpleNamespace(
            state=lambda _: SimpleNamespace(
                lifecycle=SimpleNamespace(value="active"),
                identity=SimpleNamespace(module_epoch=1),
            ),
            admission=SimpleNamespace(admit=Mock(side_effect=refusal)),
        )
        runtime._budget_policy = SimpleNamespace(
            deadline=lambda *_args, **_kw: monotonic() + 30
        )
        runtime._monotonic_clock, runtime._handler_timeout = monotonic, 30
        runtime.issuer = ContextIssuer()
        runtime._command_ingresses = {}
        runtime.owner_authority = SimpleNamespace(release=Mock())
        runtime.gateway = SimpleNamespace(
            invoke_command=AsyncMock(), invoke_tool=AsyncMock()
        )
        runtime.output = SimpleNamespace(route=AsyncMock())
        return runtime

    async def test_only_known_admission_refusal_emits_narrow_signal_before_dispatch(
        self,
    ):
        for origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL):
            for refusal in (
                CapabilityUnavailable("private provider detail"),
                AdmissionError("stale"),
            ):
                with self.subTest(origin=origin, refusal=type(refusal).__name__):
                    runtime = self.core(refusal)
                    expected = (
                        HostIngressUnavailable
                        if isinstance(refusal, CapabilityUnavailable)
                        else AdmissionError
                    )
                    with self.assertRaises(expected) as caught:
                        await runtime._invoke_host(
                            origin, "pkg/module", "read", {}, ingress=self.ingress()
                        )
                    self.assertNotIn("private", str(caught.exception))
                    runtime.gateway.invoke_command.assert_not_awaited()
                    runtime.gateway.invoke_tool.assert_not_awaited()
                    runtime.output.route.assert_not_awaited()
                    self.assertFalse(runtime.issuer._issued)
                    self.assertFalse(runtime._host_flights)

    async def test_host_feedback_requires_current_event_and_unknown_failure_stays_silent(
        self,
    ):
        for error, current, expected in (
            (HostIngressUnavailable(), True, "模块或依赖服务暂时不可用。"),
            (HostIngressUnavailable(), False, None),
            (RuntimeError("send may have started"), True, None),
        ):
            with self.subTest(error=type(error).__name__, current=current):
                runtime = AstrBotRuntime.__new__(AstrBotRuntime)
                runtime._config_error, runtime._bundle_failure_reason = None, None
                runtime._trusted_bundle, runtime._ready = True, True
                runtime._core = SimpleNamespace(
                    invoke_command=AsyncMock(side_effect=error)
                )
                runtime._bridge = SimpleNamespace(
                    parse=lambda _: CommandInvocation("pkg/module", "read", {})
                )
                runtime._make_ingress = lambda _: self.ingress()
                runtime._is_owner_command = lambda _: False
                runtime._command_current = lambda _: current
                self.assertEqual(await runtime.handle_event(object()), expected)
                runtime._core.invoke_command.assert_awaited_once()
