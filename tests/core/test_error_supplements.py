"""S2-B offline real Core receivers and controlled shared AstrBot receiver.

Native CoreRuntime/AstrBot startup, real models/network/IM are not exercised.
The consuming test module uses only SDK+stdlib; these trusted test compositions
own real Registry/Lifecycle/ContextIssuer/admission/ModuleServices/Gateway ports.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from time import monotonic

from ygl_test_subject.adapters.astrbot.web_public import project_result
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite.repositories_output import (
    SQLiteRootOutputRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteSubscriptionStore,
)
from ygl_test_subject.services.core_runtime import _HostIngressAuthority
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.output import (
    LifecycleApprovedSendScheduler,
    OutputService,
    OutputStatus,
)

import yomihime_game_link_sdk as ygl
from tests.core import test_message_context as message_fixture
from tests.fixtures.b03_runtime import _Collector, _Handler, _manifest, build_runtime
from tests.fixtures.error_supplement_module import (
    ErrorSupplementHandler,
    envelope,
    error_result,
)
from tests.host import test_message_context as controlled
from tests.services import test_output as outlet

ROOT = Path(__file__).resolve().parents[2]


class ErrorSupplementReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s2b-core-")
        self.subject = ErrorSupplementHandler()
        registry = Registry()
        alpha = dataclasses.replace(
            _manifest("alpha"),
            tools=(ygl.ToolDescriptor("error_tool", "read", {}, "read"),),
            commands=(
                ygl.CommandDescriptor("read", "read", {}, "read"),
                *_manifest("alpha").commands,
            ),
        )
        beta = _manifest("beta")
        registry.register_package(
            ygl.PackageManifest(
                "sample",
                "1.0.0",
                ygl.MODULE_ABI_VERSION,
                (alpha, beta),
                "tests",
                "MIT",
                "offline S2-B",
            ),
            {
                "alpha": ygl.ModuleHandlers(
                    {"read": self.subject, "private.read": _Handler("private.read")},
                    {"account-collector": _Collector()},
                    {},
                ),
                "beta": ygl.ModuleHandlers({"read": _Handler()}, {}, {}),
            },
        )
        self.runtime = await build_runtime(Path(self.temp.name), registry=registry)
        self.subject.services = self.runtime.services.for_module("sample/alpha")
        self.closed = False
        self.host = message_fixture._Host(monotonic)
        self.authority = _HostIngressAuthority(
            self.host.validate, self.host.current, self.require_accepting
        )
        self.gateway = Gateway(
            registry,
            self.runtime.issuer,
            admission=self.runtime.lifecycle.admission,
            lifecycle=self.runtime.lifecycle,
        )
        self.messages = outlet._MessagePort()
        self.output = OutputService(
            issuer=self.runtime.issuer,
            admission=self.runtime.lifecycle.admission,
            send_scheduler=LifecycleApprovedSendScheduler(self.runtime.lifecycle),
            registry=registry,
            renderer=outlet._Renderer(),
            limits=ygl.DisplayLimits(2, 1024),
            conversations=outlet._Conversations(
                ygl.ConversationRef(
                    "test-adapter", ygl.ConversationKind.GROUP, "room", "test-route"
                )
            ),
            message_port=self.messages,
            deliveries=SQLiteDeliveryRepository(self.runtime.database),
            grants=self.runtime.repositories.grant_revocation,
            subscriptions=SQLiteSubscriptionStore(self.runtime.database),
            root_outputs=SQLiteRootOutputRepository(self.runtime.database),
            resource_visibility=outlet._ResourceVisibility(self.runtime.database),
            principal_resolver=InvocationPrincipalResolver(
                self.runtime.issuer,
                self.runtime.repositories.identities,
                identity_namespace="test-users",
                admission=self.runtime.lifecycle.admission,
            ),
            claim_lease=timedelta(minutes=1),
            send_timeout=5,
        )
        self.trace = []

    async def asyncTearDown(self):
        self.closed = True
        for view in tuple(self.runtime.issuer._issued.values()):
            self.runtime.issuer.release(view)
        for module_id in ("sample/alpha", "sample/beta"):
            await self.runtime.lifecycle.stop(module_id)
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)
        self.temp.cleanup()

    def require_accepting(self):
        if self.closed:
            raise ygl.InvalidInvocation()

    async def receive(
        self, original, *, route=False, expected_output=OutputStatus.TOOL_RESULT
    ):
        source = message_fixture._Source()
        ingress = self.host.ingress(source)
        accepted = await self.authority.validate(ygl.InvocationOrigin.LLM_TOOL, ingress)
        self.assertIs(accepted.ingress, ingress)
        snapshot = self.runtime.registry.snapshot()
        module = snapshot.module("sample/alpha")
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.LLM_TOOL,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id=ingress.actor_id,
            conversation_id=ingress.conversation_id,
            adapter_id=ingress.adapter_id,
            capability_id="read",
            deadline=monotonic() + 30,
        )
        try:
            self.runtime.lifecycle.admission.admit(view, "read")
            self.authority.attach_message(self.runtime.issuer, view, accepted)
            scope = await self.subject.services.scopes.bind(view)
            self.assertEqual(scope.invocation.invocation_id, view.invocation_id)
            self.trace.append("actual_authority_issue_admit_bind")
            before = len(self.subject.calls)
            self.subject.result = original
            result = await self.gateway.invoke_tool(view, "error_tool", {})
            self.assertEqual(len(self.subject.calls), before + 1)
            self.assertEqual(self.subject.calls[-1].invocation_id, view.invocation_id)
            self.assertIsNotNone(self.subject.bound)
            self.trace.append("actual_handler_bound_Gateway_result_receiver")
            if route:
                published = await self.output.route(view, result)
                self.last_output = published
                self.assertIs(published.status, expected_output)
                if expected_output is OutputStatus.TOOL_RESULT:
                    self.assertEqual(
                        published.result.facts.facts, result.model_facts.facts
                    )
                self.trace.append(
                    "actual_OutputService_public_ToolOutput_claim_receipt"
                )
            return result
        finally:
            if view.invocation_id in self.runtime.issuer._issued:
                self.runtime.issuer.release(view)

    async def plain_control(self):
        result = await self.receive(error_result(envelope()))
        self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertEqual(result.error, error_result().error)
        self.assertIsNotNone(result.model_facts)
        self.assertEqual(dict(result.model_facts.facts), envelope())
        projected = project_result(result)
        self.assertEqual(projected["error"]["code"], "no_records")
        self.assertEqual(
            projected["model_facts"]["error"]["message"], result.error.message
        )
        self.assertEqual(
            projected["error"]["message"], "查询未完成，请检查输入或稍后重试。"
        )
        self.assertFalse(self.runtime.transport.requests)
        return result

    async def test_plain_error_actual_core_and_output_positive_control(self):
        for original in (error_result(), error_result(envelope())):
            with self.subTest(facts=original.model_facts is not None):
                result = await self.receive(
                    original, route=original.model_facts is not None
                )
                self.assertEqual(result.error, original.error)
                self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertFalse(self.messages.calls)
        self.assertFalse(self.runtime.transport.requests)

    async def test_non_ff_supplement_is_received_without_business_selector(self):
        await self.plain_control()
        supplement = {
            "attempted_source": "archive",
            "reason_category": "empty",
            "limits": {"requested": 3, "available": None, "retry": False},
        }
        result = await self.receive(error_result(envelope(supplement=supplement)))
        self.assertEqual(
            result.error,
            error_result().error,
            "legal non-FF supplement rejected at actual Core receiver",
        )
        self.assertIsNotNone(result.model_facts)
        self.assertEqual(result.model_facts.facts["supplement"], supplement)
        self.assertFalse(self.messages.calls)

    async def test_finite_fact_float_survives_actual_gateway_rebuild(self):
        await self.plain_control()
        original = ygl.CapabilityResult(
            "neutral-number",
            ygl.ResultStatus.SUCCESS,
            document=ygl.DisplayDocument(
                "Number", "Reading", (ygl.TextBlock("public reading"),)
            ),
            model_facts=ygl.FactDocument({"temperature": 1.25}),
        )
        result = await self.receive(original)
        self.assertIs(
            result.status,
            ygl.ResultStatus.SUCCESS,
            "finite fact float rejected at receiver/rebuild",
        )
        self.assertEqual(result.model_facts.facts["temperature"], 1.25)
        self.assertIs(type(result.model_facts.facts["temperature"]), float)

    async def test_old_top_level_market_is_rejected_by_neutral_error_envelope(self):
        await self.plain_control()
        result = await self.receive(
            error_result(envelope(market={"opaque_other_business": "empty"}))
        )
        self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertEqual(
            result.error.code,
            ygl.ErrorCode.UNKNOWN,
            "old market special key still accepted by Core",
        )
        self.assertIsNone(result.model_facts)
        self.assertFalse(self.messages.calls)

    async def test_malformed_error_facts_are_safely_rejected_at_actual_receiver(self):
        await self.plain_control()
        cycle = []
        cycle.append(cycle)
        invalid = (
            envelope(unknown="no"),
            {"status": "error"},
            envelope(error={"code": "no_records", "message": "different"}),
            envelope(supplement={"unsafe": object()}),
            envelope(supplement={"cycle": cycle}),
            envelope(supplement={"nonfinite": float("nan")}),
        )
        for index, facts in enumerate(invalid):
            with self.subTest(case=index):
                result = await self.receive(error_result(facts))
                self.assertIs(result.status, ygl.ResultStatus.ERROR)
                self.assertEqual(result.error.code, ygl.ErrorCode.UNKNOWN)
                self.assertIsNone(result.model_facts)
        self.assertFalse(self.messages.calls)
        self.assertFalse(self.runtime.transport.requests)

    @staticmethod
    def raw_error(facts, *, sources=()):
        # Canonical descriptive DTO bypass; no new invocation/source authority.
        document = object.__new__(ygl.FactDocument)
        object.__setattr__(document, "facts", facts)
        object.__setattr__(document, "sources", sources)
        object.__setattr__(document, "schema_version", "1.8.0")
        return dataclasses.replace(error_result(), model_facts=document)

    async def assert_received(self, facts, *, sources=(), route=True):
        result = await self.receive(self.raw_error(facts, sources=sources), route=route)
        self.assertEqual(result.error, error_result().error)
        self.assertIsNotNone(result.model_facts)
        return result

    async def assert_rejected(self, facts, *, sources=()):
        before = len(self.messages.calls)
        result = await self.receive(self.raw_error(facts, sources=sources))
        self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertEqual(result.error.code, ygl.ErrorCode.UNKNOWN)
        self.assertIsNone(result.model_facts)
        self.assertEqual(len(self.messages.calls), before)
        return result

    async def test_error_supplement_exact_shape_identity_and_json_domain(self):
        from collections.abc import Mapping
        from decimal import Decimal

        await self.plain_control()
        for supplement in (
            {},
            {"market": {"price": 1.25, "missing": None}},
            {"other": [True, False, 4, -2, 0.125, "safe"]},
        ):
            with self.subTest(legal=supplement):
                await self.assert_received(envelope(supplement=supplement))

        class ThrowingMapping(Mapping):
            def __iter__(self):
                raise RuntimeError("private mapping sentinel")

            def __getitem__(self, key):
                raise RuntimeError("private mapping sentinel")

            def __len__(self):
                return 1

        invalid = [
            {},
            {"error": envelope()["error"]},
            {"status": "error"},
            {"status": "success", "error": envelope()["error"]},
            envelope(
                error={"code": "unknown", "message": error_result().error.message}
            ),
            envelope(error={"code": "no_records", "message": "not the detail"}),
            envelope(error={"code": "no_records"}),
            envelope(error={**envelope()["error"], "extra": "forbidden"}),
            envelope(supplement=[]),
            envelope(supplement=None),
            envelope(supplement={"": "empty key"}),
            envelope(supplement={1: "integer key"}),
            envelope(supplement={"unsupported": b"bytes"}),
            envelope(supplement={"unsupported": ygl.NumberValue(2)}),
            envelope(supplement={"unsupported": Decimal("1.25")}),
            envelope(supplement={"nonfinite": float("inf")}),
            envelope(supplement={"nonfinite": float("-inf")}),
            envelope(supplement=ThrowingMapping()),
        ]
        for index, facts in enumerate(invalid):
            with self.subTest(invalid=index):
                await self.assert_rejected(facts)
        with self.subTest(error_document=True):
            malformed = dataclasses.replace(
                error_result(envelope()),
                document=ygl.DisplayDocument(
                    "Invalid", "Error", (ygl.TextBlock("hidden"),)
                ),
            )
            result = await self.receive(malformed)
            self.assertEqual(result.error.code, ygl.ErrorCode.UNKNOWN)
            self.assertIsNone(result.model_facts)
        with self.subTest(nonerror_error=True):
            malformed = dataclasses.replace(
                error_result(),
                status=ygl.ResultStatus.SUCCESS,
                document=ygl.DisplayDocument(
                    "Invalid", "Success", (ygl.TextBlock("hidden"),)
                ),
            )
            result = await self.receive(malformed)
            self.assertEqual(result.error.code, ygl.ErrorCode.UNKNOWN)
            self.assertIsNone(result.document)

    async def test_error_total_nodes_and_sources_have_inclusive_bounds(self):
        # Independently enumerated: envelope root/status/error = 9 nodes;
        # supplement key/object adds2; items key/array adds2 => 13+N.
        for count in (499, 500):
            with self.subTest(nodes=13 + count):
                facts = envelope(supplement={"items": [None] * count})
                if count == 499:
                    await self.assert_received(facts)
                else:
                    await self.assert_rejected(facts)
        # Every source counts one root-depth node, even repeated equal strings.
        await self.assert_received(
            envelope(supplement={"items": [None] * 498}), sources=("source",)
        )
        await self.assert_rejected(
            envelope(supplement={"items": [None] * 499}), sources=("source",)
        )
        for count in (248, 249):
            shared = [None] * count
            facts = envelope(supplement={"left": shared, "right": shared})
            # 11 + 4(two keys/two arrays) + 2*N = 511 or513;
            # aliases aren't cycles and do not earn a counting discount.
            with self.subTest(alias_total=15 + 2 * count):
                if count == 248:
                    await self.assert_received(facts)
                else:
                    await self.assert_rejected(facts)

    async def test_error_depth_root_and_string_key_source_limits(self):
        for levels in (14, 15):
            value = None
            for _ in range(levels):
                value = [value]
            # Root0 -> supplement1 -> path value2 -> leaf2+levels.
            with self.subTest(depth=2 + levels):
                if levels == 14:
                    await self.assert_received(envelope(supplement={"path": value}))
                else:
                    await self.assert_rejected(envelope(supplement={"path": value}))
        for dimension in ("value", "key", "source"):
            for length in (4096, 4097):
                value = "汉" * length
                facts = envelope(
                    supplement={value: None}
                    if dimension == "key"
                    else {"value": value}
                    if dimension == "value"
                    else {}
                )
                sources = (value,) if dimension == "source" else ()
                with self.subTest(dimension=dimension, characters=length):
                    if length == 4096:
                        await self.assert_received(facts, sources=sources)
                    else:
                        await self.assert_rejected(facts, sources=sources)

    async def test_error_tool_serialized_utf8_exact_limit_and_web_independent_cap(self):
        import json

        # 63 full ASCII strings + one bounded tail. The independent actual
        # default Tool JSON encoding measures overhead, not a copy of guard math.
        chunks = ["x" * 4096] * 63 + [""]
        empty_tail_bytes = len(
            json.dumps(
                envelope(supplement={"chunks": chunks}),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        )
        tail_bytes = 256 * 1024 - empty_tail_bytes
        self.assertGreater(tail_bytes, 0)
        self.assertLessEqual(tail_bytes, 4096)
        chunks[-1] = "汉" * (tail_bytes // 3) + "x" * (tail_bytes % 3)
        facts = envelope(supplement={"chunks": chunks})
        self.assertEqual(
            len(json.dumps(facts, ensure_ascii=False, allow_nan=False).encode("utf-8")),
            256 * 1024,
        )
        result = await self.assert_received(facts)
        from ygl_test_subject.adapters.astrbot.web_public import WebPublicRejected

        with self.assertRaises(WebPublicRejected):
            project_result(result)  # Web response adds fields around these facts.
        chunks[-1] += "x"
        too_large = envelope(supplement={"chunks": chunks})
        self.assertEqual(
            len(
                json.dumps(too_large, ensure_ascii=False, allow_nan=False).encode(
                    "utf-8"
                )
            ),
            256 * 1024 + 1,
        )
        await self.assert_rejected(too_large)
        self.trace.append(
            "ERROR_Tool_facts_262144_pass_262145_refused_Web_whole_response_independent"
        )

    async def test_fact_float_does_not_widen_display_and_old_decimal_is_preserved(self):
        from decimal import Decimal

        good = ygl.CapabilityResult(
            "decimal-facts",
            ygl.ResultStatus.SUCCESS,
            document=ygl.DisplayDocument(
                "Decimal", "Reading", (ygl.TextBlock("safe"),)
            ),
            model_facts=ygl.FactDocument({"exact": Decimal("1.250"), "json": 1.25}),
        )
        result = await self.receive(good, route=True)
        self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
        self.assertEqual(result.model_facts.facts["exact"], Decimal("1.250"))
        self.assertIs(type(result.model_facts.facts["exact"]), Decimal)
        bad = dataclasses.replace(
            good,
            document=ygl.DisplayDocument(
                "Float", "Invalid", (ygl.FieldsBlock({"reading": 1.25}),)
            ),
        )
        result = await self.receive(bad)
        self.assertEqual(result.error.code, ygl.ErrorCode.UNKNOWN)
        self.assertIsNone(result.document)
        self.assertIsNone(result.model_facts)

    async def test_original_json_is_detached_before_awaited_output_projection(self):
        import asyncio

        nested = {"items": [1.25, {"public": "original"}]}
        original = error_result(envelope(supplement=nested))

        class MutatingResources:
            async def contains_non_public_resource_reference(self, text):
                await asyncio.sleep(0)
                nested["items"][0] = float("nan")
                nested["items"][1]["public"] = "caller mutation"
                return False

        self.output._resource_visibility = MutatingResources()
        result = await self.receive(original, route=True)
        self.assertEqual(result.model_facts.facts["supplement"]["items"][0], 1.25)
        self.assertEqual(
            result.model_facts.facts["supplement"]["items"][1]["public"], "original"
        )
        with self.assertRaises(TypeError):
            result.model_facts.facts["supplement"]["items"][1]["public"] = "rewrite"
        self.assertFalse(self.messages.calls)

    async def test_awaited_tool_completion_revocation_refuses_late_payload(self):
        repository = self.output._root_outputs
        owner = self

        class CompletingThenRevoking:
            completed = None

            async def claim(self, *args, **kwargs):
                return await repository.claim(*args, **kwargs)

            async def complete(self, *args, **kwargs):
                self.completed = await repository.complete(*args, **kwargs)
                view = owner.subject.calls[-1]
                owner.runtime.issuer.release(view)
                return self.completed

        port = CompletingThenRevoking()
        self.output._root_outputs = port
        result = await self.receive(
            error_result(envelope(supplement={"public": "safe"})),
            route=True,
            expected_output=OutputStatus.FAILED,
        )
        self.assertEqual(result.error, error_result().error)
        self.assertIsNotNone(
            port.completed
        )  # Accepted terminal claim is not rolled back.
        self.assertIsNone(self.last_output.result)
        self.assertEqual(self.last_output.error_code, "invocation_unavailable")
        self.assertFalse(self.messages.calls)
        self.trace.append(
            "actual_SQLite_completed_claim_retained_late_public_payload_denied"
        )

    async def test_existing_mapping_input_stops_before_eager_materialization(self):
        from collections.abc import Mapping

        await self.plain_control()

        class OversizedMapping(Mapping):
            reads = 0
            items_used = False

            def __iter__(self):
                for index in range(1000000):
                    yield "k" + str(index)

            def __getitem__(self, key):
                self.reads += 1
                return None

            def __len__(self):
                return 1000000

            def items(self):
                self.items_used = True
                raise RuntimeError("eager mapping materialization must not run")

        value = OversizedMapping()
        # Bypass the descriptive DTO's constructor only. Host source/root/bind
        # authority is still issued by the exact actual Core path above.
        await self.assert_rejected(envelope(supplement=value))
        self.assertFalse(value.items_used)
        self.assertGreater(value.reads, 0)
        # Root envelope9 + supplement key/object2 + each map key/value2.
        # Pair251 reaches key512 then value513; no million-entry traversal.
        self.assertLessEqual(value.reads, 251)
        self.trace.append(
            "existing_Mapping_domain_incremental_512_budget_before_generic_items"
        )

    async def test_web_depth_and_whole_response_bounds_are_independent(self):
        import json

        from ygl_test_subject.adapters.astrbot.web_public import WebPublicRejected

        for levels in (6, 7):
            value = None
            for _ in range(levels):
                value = [value]
            result = await self.assert_received(envelope(supplement={"nest": value}))
            # Core root0/supplement1/nest2 + six orseven arrays = leaf8/9.
            with self.subTest(Web_depth=2 + levels):
                if levels == 6:
                    self.assertIsNotNone(project_result(result)["model_facts"])
                else:
                    with self.assertRaises(WebPublicRejected):
                        project_result(result)
        chunks = ["x" * 4096] * 63 + [""]
        low = await self.assert_received(envelope(supplement={"chunks": chunks}))
        data = project_result(low)

        def web_bytes(data):
            return len(
                json.dumps(
                    {"status": "ok", "message": "", "data": data},
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )

        tail = 256 * 1024 - web_bytes(data)
        self.assertGreater(tail, 0)
        self.assertLessEqual(tail, 4096)
        chunks[-1] = "x" * tail
        exact = await self.assert_received(envelope(supplement={"chunks": chunks}))
        exact_data = project_result(exact)
        self.assertEqual(web_bytes(exact_data), 256 * 1024)
        chunks[-1] += "x"
        # Core still admits the complete facts body. Only the independently
        # larger whole Web response is over its byte limit.
        over = await self.assert_received(envelope(supplement={"chunks": chunks}))
        with self.assertRaises(WebPublicRejected):
            project_result(over)
        self.trace.append(
            "Web_depth8_pass9_refused_whole_response262144_pass262145_refused"
        )

    async def test_neutral_supplement_owned_publisher_chain(self):
        import json
        from types import SimpleNamespace

        from ygl_test_subject.adapters.astrbot.runtime import (
            _AstrBotMessageIngress,
            _MessageReceipts,
        )
        from ygl_test_subject.core.ports import RootOutputOutcome, RootOutputState

        # A controlled Host owns genuine composition readiness and a genuine
        # publication, not AstrBotRuntime.start/initialize flags or a DTO proof.
        with controlled.tool_contracts() as host:

            class Context(host.Context, controlled.legacy.fixture._Context):
                def __init__(self):
                    controlled.legacy.fixture._Context.__init__(self)
                    manager = host.Manager.__new__(host.Manager)
                    manager.func_list = []
                    self.provider_manager = SimpleNamespace(llm_tools=manager)

            context = Context()
            receipts = _MessageReceipts()
            owned = {}
            receiver = _AstrBotMessageIngress(
                object(), receipts, lambda handle: owned["host"].current_owner(handle)
            )
            authority = _HostIngressAuthority(
                receiver.validate,
                receiver.current,
                lambda: owned["host"].require_accepting(),
            )
            current = controlled._OfflineHost(
                self.runtime, context, receiver, authority, self.gateway, self.output
            )
            owned["host"] = current
            current.accepting = True
            try:
                current.publisher.publish()
                wrapper = controlled.legacy.LLMToolTests.wrapper(
                    SimpleNamespace(host=host, context=context), "查公开来源状态"
                )
                manager = context.provider_manager.llm_tools
                tool = manager.get_full_tool_set().get_tool("error_tool")
                self.subject.result = error_result(
                    envelope(
                        supplement={
                            "archive": {"available": None, "confidence": 0.25},
                            "recovery_hint": "Choose another public source.",
                        }
                    )
                )
                before = len(self.subject.calls)
                result = json.loads(await tool.call(wrapper))
                self.assertEqual(len(self.subject.calls), before + 1)
                invocation = self.subject.calls[-1]
                self.assertEqual(
                    self.subject.bound.invocation.invocation_id,
                    invocation.invocation_id,
                )
                self.assertIs(invocation.origin, ygl.InvocationOrigin.LLM_TOOL)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error"], envelope()["error"])
                self.assertEqual(
                    result["supplement"]["archive"],
                    {"available": None, "confidence": 0.25},
                )
                self.assertEqual(
                    result["supplement"]["recovery_hint"],
                    "Choose another public source.",
                )
                claim = await self.runtime.database.executor.run_read(
                    lambda unit: tuple(
                        unit.execute(
                            "SELECT state, output_outcome FROM b04_root_outputs WHERE root_invocation_id=?",
                            (invocation.invocation_id,),
                        ).fetchone()
                    )
                )
                self.assertEqual(claim[0], RootOutputState.COMPLETED.value)
                self.assertEqual(claim[1], RootOutputOutcome.TOOL_RETURNED.value)
                self.assertFalse(self.messages.calls)
                self.assertFalse(self.runtime.transport.requests)
                self.assertFalse(self.runtime.issuer._issued)
                current.publisher.revoke()
                with self.assertRaises(PermissionError):
                    await tool.call(wrapper)
                self.assertEqual(len(self.subject.calls), before + 1)
                self.trace.append(
                    "E07_actual_neutral_owned_publication_bind_Gateway_Output_terminal_claim_no_HTTP_send"
                )
            finally:
                current.accepting = False
                receipts.close()
                current.publisher.revoke()
                current.publisher.cleanup()


class ErrorSupplementMarketProducerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Delegate the already accepted controlled composition; no native start
        # or readiness/security gate replacement, no handler direct invocation.
        self.host_case = controlled.ControlledMessageTests(
            methodName="test_s2a_controlled_host_to_real_core_and_ff14"
        )
        self.host_case.temporary = tempfile.TemporaryDirectory(prefix="s2b-host-")
        self.host_case.root = Path(self.host_case.temporary.name)
        await self.host_case.start()
        self.trace = []

    async def asyncTearDown(self):
        await self.host_case.asyncTearDown()

    async def test_actual_ff_empty_error_moves_market_into_supplement(self):
        case = self.host_case
        normal = await case.call(
            parameters={"query": "44091", "region": "cn"},
            wrapper=case.wrapper("物品ID 44091多少钱"),
        )
        self.assertEqual(normal["status"], "success")
        self.assertEqual(normal["market"]["item_id"], 44091)
        self.trace.append(
            "shared_AstrBot_receiver_Core_FF_Gateway_Output_ownedTool_positive"
        )

        async def empty(request):
            if "/aggregated/" in request.path:
                return ygl.HttpResponse(200, {}, b'{"results":[],"failedItems":[]}')
            return None

        case.transport.callback = empty
        before = len(case.transport.requests)
        result = await case.call(
            parameters={"query": "44092", "region": "cn"},
            wrapper=case.wrapper("物品ID 44092多少钱"),
        )
        self.assertGreater(len(case.transport.requests), before)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "no_records")
        self.trace.append(
            "actual_FF_empty_producer_no_records_after_Core_Output_publisher"
        )
        self.assertNotIn(
            "market",
            result,
            "actual FF ERROR producer still emits old top-level market",
        )
        self.assertEqual(
            result["supplement"]["market"]["coverage"][0]["state"], "empty"
        )
        self.assertEqual(result["supplement"]["market"]["minimums"], [])

    async def test_actual_ff_error_white_selection_blocks_source_and_exception_canaries(
        self,
    ):
        import json

        case = self.host_case
        positive = await case.call(
            parameters={"query": "44091", "region": "cn"},
            wrapper=case.wrapper("物品ID 44091多少钱"),
        )
        self.assertEqual(positive["status"], "success")

        async def empty(request):
            if "/aggregated/" in request.path:
                return ygl.HttpResponse(
                    200,
                    {"x-private": "PRIVATE_HEADER_CANARY"},
                    b'{"results":[],"failedItems":[],"credentials":"PRIVATE_BODY_CANARY","proof":"PRIVATE_PROOF_CANARY"}',
                )
            return None

        case.transport.callback = empty
        result = await case.call(
            parameters={"query": "44092", "region": "cn"},
            wrapper=case.wrapper("物品ID 44092多少钱 PRIVATE_MESSAGE_CANARY"),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "no_records")
        self.assertEqual(set(result), {"status", "error", "supplement"})
        self.assertEqual(
            result["supplement"]["market"]["coverage"][0]["state"], "empty"
        )
        self.assertEqual(result["supplement"]["market"]["minimums"], [])
        self.assertIn("等待用户明确新指令", result["error"]["message"])
        for canary in (
            "PRIVATE_HEADER_CANARY",
            "PRIVATE_BODY_CANARY",
            "PRIVATE_PROOF_CANARY",
            "PRIVATE_MESSAGE_CANARY",
        ):
            self.assertNotIn(canary, json.dumps(result, ensure_ascii=False))

        # A separate query's transport exception is reduced to safe diagnostics,
        # not exception text/attributes. Clear only the test transport cache by
        # using a different accepted item and scope, not production cache state.
        async def unavailable(request):
            if "/aggregated/" in request.path:
                exc = RuntimeError("PRIVATE_EXCEPTION_CANARY")
                exc.credentials = "PRIVATE_EXCEPTION_CREDENTIAL_CANARY"
                raise exc
            return None

        case.transport.callback = unavailable
        failure = await case.call(
            parameters={"query": "44092", "region": "global"},
            wrapper=case.wrapper("全球物品ID 44092多少钱"),
        )
        self.assertEqual(failure["status"], "error")
        self.assertIn("等待用户明确新指令", failure["error"]["message"])
        self.assertNotIn("PRIVATE_EXCEPTION", json.dumps(failure, ensure_ascii=False))
        self.trace.append(
            "actual_owned_tool_Core_FF_error_source_body_headers_exception_message_white_selection"
        )

    async def test_actual_ff_all_source_transport_failures_have_exact_public_diagnostics(
        self,
    ):
        import json

        case = self.host_case
        # A fresh per-method real composition proves receipt/binding/handler/
        # Output readiness; item 44092 has never been queried in this repository.
        positive = await case.call(
            parameters={"query": "44091", "region": "cn"},
            wrapper=case.wrapper("物品ID 44091多少钱"),
        )
        self.assertEqual(positive["status"], "success")
        self.assertEqual(positive["market"]["item_id"], 44091)
        self.assertTrue(any("/aggregated/" in r.path for r in case.transport.requests))
        requests = []

        async def unavailable(request):
            if "/aggregated/" in request.path:
                requests.append(request.path)
                exc = RuntimeError("PRIVATE_EXCEPTION_CANARY")
                exc.credentials = "PRIVATE_EXCEPTION_CREDENTIAL_CANARY"
                raise exc
            return None

        case.transport.callback = unavailable
        before = len(case.transport.requests)
        result = await case.call(
            parameters={"query": "44092", "region": "global"},
            wrapper=case.wrapper("全球物品ID 44092多少钱 PRIVATE_MESSAGE_CANARY"),
        )
        regions = ("North-America", "Europe", "Japan", "Oceania")
        self.assertEqual(len(requests), 4)
        self.assertEqual({p.split("/")[-2] for p in requests}, set(regions))
        self.assertTrue(all(p.split("/")[-1] == "44092" for p in requests))
        self.assertEqual(len(case.transport.requests) - before, 4)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "upstream_error")
        self.assertEqual(set(result), {"status", "error", "supplement"})
        self.assertIn("市场来源查询未完成", result["error"]["message"])
        self.assertIn("等待用户明确新指令", result["error"]["message"])
        market = result["supplement"]["market"]
        self.assertEqual(market["item_id"], 44092)
        self.assertEqual(market["minimums"], [])
        self.assertEqual(market["listings"], [])
        self.assertIn("等待用户明确新指令", market["answer_guidance"])
        self.assertEqual(len(market["coverage"]), 4)
        self.assertEqual({row["region"] for row in market["coverage"]}, set(regions))
        for row in market["coverage"]:
            self.assertEqual(row["target"], row["region"])
            self.assertEqual(row["state"], "failed")
            self.assertEqual(row["stage"], "http")
            self.assertEqual(row["reason"], "transport_failed")
            self.assertEqual(row["quotes"], [])
            for field in (
                "status_code",
                "cached",
                "fetched_at",
                "fetched_age_seconds",
                "source",
                "incomplete",
                "truncated",
            ):
                self.assertIsNone(row[field], field)
        for canary in (
            "PRIVATE_EXCEPTION_CANARY",
            "PRIVATE_EXCEPTION_CREDENTIAL_CANARY",
            "PRIVATE_MESSAGE_CANARY",
        ):
            self.assertNotIn(canary, json.dumps(result, ensure_ascii=False))
        self.trace.append(
            "R1_I3_fresh_actual_owned_Core_FF_Output_four_HTTP_transport_failed_exact_upstream_error"
        )

    async def test_actual_command_error_has_no_message_and_web_projection_is_safe(self):
        case = self.host_case
        current = case.runtime
        from ygl_test_subject.services.identity import (
            TrustedIngressPrincipalProvisioner,
        )

        provisioner = TrustedIngressPrincipalProvisioner(
            case.b03.repositories.identities, identity_namespace="test-users"
        )
        current.output._conversations = outlet._Conversations(
            ygl.ConversationRef(
                "test-adapter", ygl.ConversationKind.GROUP, "room", "test-route"
            )
        )

        async def command(item):
            wrapper = case.wrapper(
                "物品ID " + str(item),
                sender="alice",
                session="room",
                adapter="test-adapter",
            )
            ingress = current.receiver.make(
                wrapper.context.event, origin=ygl.InvocationOrigin.COMMAND
            )
            accepted = await current.authority.validate(
                ygl.InvocationOrigin.COMMAND, ingress
            )
            self.assertIs(accepted.ingress, ingress)
            snapshot = current.registry.snapshot()
            module = snapshot.module("ff14/ff14")
            self.assertTrue(
                any(c.operation_path == "market" for c in module.manifest.commands)
            )
            await provisioner.ensure(
                ingress.actor_id
            )  # Same production post-validation COMMAND step.
            view = case.b03.issuer.issue(
                origin=ygl.InvocationOrigin.COMMAND,
                module_id=module.module_id,
                module_epoch=module.epoch,
                registry_revision=snapshot.revision,
                actor_id=ingress.actor_id,
                conversation_id=ingress.conversation_id,
                adapter_id=ingress.adapter_id,
                capability_id="ff14.market.query",
                deadline=monotonic() + 30,
            )
            try:
                current.lifecycle.admission.admit(view, "ff14.market.query")
                handler = module.handlers.capabilities["ff14.market.query"]
                bound = await handler.services.scopes.bind(view)
                with self.assertRaises(ygl.ServiceUnavailable) as no_message:
                    await bound.message.read()
                self.assertIs(type(no_message.exception), ygl.ServiceUnavailable)
                self.assertEqual(str(no_message.exception), "service is unavailable")
                self.assertIsNone(no_message.exception.__cause__)
                self.assertIsNone(no_message.exception.__context__)
                self.assertIsNone(
                    await bound.cache.get("s2b-command-positive-precondition")
                )
                result = await current.gateway.invoke_command(
                    view, "market", {"command": str(item) + " region=cn"}
                )
                if result.status is ygl.ResultStatus.ERROR:
                    routed = await current.output.route(view, result)
                    self.assertIs(routed.status, OutputStatus.SENT)
                return result
            finally:
                case.b03.issuer.release(view)

        positive = await command(44091)
        self.assertIs(positive.status, ygl.ResultStatus.SUCCESS)

        async def empty(request):
            if "/aggregated/" in request.path:
                return ygl.HttpResponse(
                    200,
                    {"x-private": "PRIVATE_HEADER_CANARY"},
                    b'{"results":[],"failedItems":[]}',
                )
            return None

        case.transport.callback = empty
        result = await command(44092)
        self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertIs(result.error.code, ygl.ErrorCode.NO_RECORDS)
        self.assertIsNone(result.model_facts)
        self.assertIn("无法判断", result.error.message)
        self.assertEqual(
            current.output._message_port.calls[-1][1].text, "no records found"
        )
        projected = project_result(result)
        self.assertEqual(projected["error"]["code"], "no_records")
        self.assertEqual(
            projected["error"]["message"], "查询未完成，请检查输入或稍后重试。"
        )
        self.assertNotIn("PRIVATE_HEADER_CANARY", repr(projected))
        self.trace.append(
            "actual_shared_COMMAND_authority_issue_admit_bind_no_message_FF_Gateway_safe_reply"
        )
        self.trace.append(
            "independent_Web_project_result_safe_hint_not_native_Web_startup"
        )
