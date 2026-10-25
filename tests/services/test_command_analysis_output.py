"""Real OutputService authority/routing; only Host completion bytes are synthetic."""

import unittest
from dataclasses import replace
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.analysis import AstrBotCommandAnalysis
from ygl_test_subject.services.output import OutputStatus

from tests.services import test_output
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.results import AnalysisRequest


class CommandAnalysisOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = test_output.OutputServiceTests(methodName="runTest")
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)

    def result(self):
        return replace(
            self.fixture._result(),
            analysis_request=AnalysisRequest(
                "test.analysis", 1, "trusted instructions", "focus-123"
            ),
        )

    async def test_command_calls_provider_but_tool_only_publishes_facts(self):
        calls = []

        async def text_chat(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(completion_text="bounded analysis")

        provider = SimpleNamespace(text_chat=text_chat)
        service = self.fixture._service(
            analysis_port=AstrBotCommandAnalysis(
                SimpleNamespace(get_using_provider=lambda: provider)
            )
        )
        command = await service.route(self.fixture._view(), self.result())
        self.assertIs(command.status, OutputStatus.SENT)
        self.assertEqual(len(calls), 1)
        document = self.fixture.renderer.calls[0][0]
        self.assertIn("bounded analysis", document.ordered_blocks[-1].text)
        tool = await service.route(
            self.fixture._view(InvocationOrigin.LLM_TOOL), self.result()
        )
        self.assertIs(tool.status, OutputStatus.TOOL_RESULT)
        self.assertEqual(len(calls), 1)
        self.assertEqual(dict(tool.result.facts.facts), {"value": "public"})

    async def test_no_provider_still_sends_deterministic_report_with_explicit_fallback(
        self,
    ):
        result = await self.fixture.output.route(self.fixture._view(), self.result())
        self.assertIs(result.status, OutputStatus.SENT)
        self.assertIn(
            "AI分析未生成", self.fixture.renderer.calls[0][0].ordered_blocks[-1].text
        )

    async def test_module_disabled_during_provider_completion_never_sends_late_analysis(
        self,
    ):
        async def text_chat(**kwargs):
            await self.fixture._disable()
            return SimpleNamespace(completion_text="late forbidden analysis")

        service = self.fixture._service(
            analysis_port=AstrBotCommandAnalysis(
                SimpleNamespace(
                    get_using_provider=lambda: SimpleNamespace(text_chat=text_chat)
                )
            )
        )
        result = await service.route(self.fixture._view(), self.result())
        self.assertIsNot(result.status, OutputStatus.SENT)
        self.assertEqual(self.fixture.message_port.calls, [])
