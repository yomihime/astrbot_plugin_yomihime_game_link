from __future__ import annotations

import asyncio
import builtins
import copy
import unittest
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.contracts import diagnostics as schemas
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.services.diagnostics import (
    BoundDiagnostics,
    DiagnosticPolicy,
    DiagnosticStore,
)

from yomihime_game_link_sdk import (
    AccessDenied,
    DiagnosticDeclaration,
    InvalidInvocation,
    ParameterError,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "minimum": 0, "maximum": 100},
        "state": {"type": "string", "maxLength": 16, "enum": ["ok", "failed"]},
    },
    "required": ["count", "state"],
    "additionalProperties": False,
}


class DiagnosticSchemaWalkTests(unittest.TestCase):
    @staticmethod
    def root(child):
        return {"type": "object", "properties": {"item": child}}

    def test_comparison_never_exposes_writable_schema_backing(self):
        class Mutator:
            def __eq__(self, other):
                if type(other) is dict:
                    other["token"] = {"type": "string", "maxLength": 8}
                return False

        class MappingMutator(dict):
            def __eq__(self, other):
                if type(other) is dict:
                    other["token"] = {"type": "string", "maxLength": 8}
                return False

        declaration = validate_contract(DiagnosticDeclaration("SafeEvent", SCHEMA))
        frozen = declaration.payload_schema
        for layer in (frozen, frozen["properties"], frozen["properties"]["count"]):
            for attacker in (Mutator(), MappingMutator()):
                self.assertFalse(layer == attacker)
                self.assertNotIn("token", layer)
            with self.assertRaises((AttributeError, TypeError)):
                object.__setattr__(layer, "backing", {"token": True})
            self.assertIs(type(layer.items()), tuple)
        self.assertIs(validate_contract(declaration), declaration)
        self.assertIs(declaration.payload_schema, frozen)

    def test_same_class_forgery_and_foreign_wrappers_still_fully_validate(self):
        frozen = schemas.freeze_diagnostic_schema(SCHEMA)
        cls = type(frozen)
        forged = tuple.__new__(
            cls,
            tuple(
                {
                    "type": ("object",),
                    "properties": {"token": {"type": "string", "maxLength": 8}},
                }.items()
            ),
        )
        self.assertNotIn(id(forged), schemas._FROZEN_SCHEMAS)
        with self.assertRaisesRegex(ValueError, "sensitive"):
            schemas.freeze_diagnostic_schema(forged)
        with self.assertRaisesRegex(ValueError, "sensitive"):
            schemas.freeze_diagnostic_schema(MappingProxyType(dict(forged)))
        backing = {"item": {"type": "boolean"}}
        benign = tuple.__new__(cls, (("type", ("object",)), ("properties", backing)))
        detached = schemas.freeze_diagnostic_schema(benign)
        self.assertIsNot(benign, detached)
        self.assertNotIn(id(benign), schemas._FROZEN_SCHEMAS)
        backing["token"] = {"type": "string", "maxLength": 8}
        self.assertNotIn("token", detached["properties"])
        with self.assertRaisesRegex(ValueError, "sensitive"):
            schemas.freeze_diagnostic_schema(benign)

    def test_only_self_created_deep_immutable_roots_are_reused(self):
        with patch.object(schemas, "_FROZEN_SCHEMAS", {}):
            with self.assertRaises(ValueError):
                schemas.freeze_diagnostic_schema(None)
            raw = copy.deepcopy(SCHEMA)
            frozen = schemas.freeze_diagnostic_schema(raw)
            self.assertIs(schemas.freeze_diagnostic_schema(frozen), frozen)
            with patch.object(
                schemas, "_sensitive_field", wraps=schemas._sensitive_field
            ) as checked:
                foreign = MappingProxyType(dict(frozen))
                detached = schemas.freeze_diagnostic_schema(foreign)
                self.assertIsNot(detached, foreign)
                self.assertTrue(checked.call_count)
                self.assertIs(schemas.freeze_diagnostic_schema(detached), detached)
            raw["properties"]["token"] = {"type": "string", "maxLength": 8}
            with self.assertRaises(ValueError):
                schemas.freeze_diagnostic_schema(raw)
            self.assertNotIn("token", frozen["properties"])

    def test_subclass_type_and_required_leaves_are_never_trusted_outputs(self):
        class Token(str):
            pass

        class SpoofedType(type):
            def __eq__(self, other):
                return other is str

            def __hash__(self):
                return hash(str)

        class SpoofedToken(str, metaclass=SpoofedType):
            pass

        for raw in (
            self.root({"type": (Token("string"),), "maxLength": 16}),
            self.root({"type": (SpoofedToken("string"),), "maxLength": 16}),
            {
                "type": "object",
                "properties": {"item": {"type": "boolean"}},
                "required": [Token("item")],
            },
        ):
            with self.subTest(raw=raw), patch.object(schemas, "_FROZEN_SCHEMAS", {}):
                frozen = schemas.freeze_diagnostic_schema(raw)
                self.assertNotIn(id(frozen), schemas._FROZEN_SCHEMAS)
                with patch.object(
                    schemas, "_sensitive_field", wraps=schemas._sensitive_field
                ) as checked:
                    again = schemas.freeze_diagnostic_schema(frozen)
                    self.assertIsNot(again, frozen)
                    self.assertTrue(checked.call_count)

    def test_output_cache_is_bounded_thread_safe_and_holds_exact_roots(self):
        with patch.object(schemas, "_FROZEN_SCHEMAS", {}):
            first = schemas.freeze_diagnostic_schema(copy.deepcopy(SCHEMA))
            for _ in range(schemas._FROZEN_SCHEMA_LIMIT):
                schemas.freeze_diagnostic_schema(copy.deepcopy(SCHEMA))
            self.assertEqual(len(schemas._FROZEN_SCHEMAS), schemas._FROZEN_SCHEMA_LIMIT)
            self.assertNotIn(id(first), schemas._FROZEN_SCHEMAS)
            self.assertIsNot(schemas.freeze_diagnostic_schema(first), first)
            trusted = schemas.freeze_diagnostic_schema(copy.deepcopy(SCHEMA))
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(
                    executor.map(schemas.freeze_diagnostic_schema, [trusted] * 64)
                )
                fresh = list(
                    executor.map(
                        schemas.freeze_diagnostic_schema,
                        [copy.deepcopy(SCHEMA) for _ in range(64)],
                    )
                )
            self.assertTrue(all(value is trusted for value in results))
            self.assertTrue(all(value is not trusted for value in fresh))
            self.assertTrue(
                all(
                    key == id(value[0])
                    for key, value in schemas._FROZEN_SCHEMAS.items()
                )
            )
            self.assertLessEqual(
                len(schemas._FROZEN_SCHEMAS), schemas._FROZEN_SCHEMA_LIMIT
            )

    def test_single_builtin_types_avoid_per_node_type_sets(self):
        schema = {
            "type": ("object",),
            "properties": {
                f"field{i}": {"type": ("string",), "maxLength": 16} for i in range(128)
            },
        }
        type_sets = 0

        def counted(value=()):
            nonlocal type_sets
            if type(value) is tuple and value in (("object",), ("string",)):
                type_sets += 1
            return builtins.set(value)

        with patch.object(schemas, "set", counted, create=True):
            frozen = schemas.freeze_diagnostic_schema(schema)
        self.assertEqual(len(frozen["properties"]), 128)
        self.assertEqual(type_sets, 0)
        self.assertEqual(frozen["properties"]["field127"]["maxLength"], 16)

    def test_nullable_order_and_noncanonical_members_keep_original_semantics(self):
        for kinds in (("string", "null"), ("null", "string"), ["string"]):
            with self.subTest(kinds=kinds):
                frozen = schemas.freeze_diagnostic_schema(
                    self.root({"type": kinds, "maxLength": 16})
                )
                self.assertEqual(frozen["properties"]["item"]["type"], tuple(kinds))

        class Token(str):
            hashes = 0

            def __hash__(self):
                self.hashes += 1
                return super().__hash__()

        token = Token("string")
        frozen = schemas.freeze_diagnostic_schema(
            self.root({"type": (token,), "maxLength": 16})
        )
        self.assertIs(frozen["properties"]["item"]["type"][0], token)
        self.assertGreaterEqual(token.hashes, 3)
        for kinds in (
            "unsupported",
            ("unsupported",),
            ("string", "string"),
            ("string", "integer"),
            Token("string"),
            type("TupleSubclass", (tuple,), {})(("string",)),
            type("ListSubclass", (list,), {})(["string"]),
        ):
            with self.subTest(kind_type=type(kinds)), self.assertRaises(ValueError):
                schemas.freeze_diagnostic_schema(
                    self.root({"type": kinds, "maxLength": 16})
                )

    def test_mutable_backing_is_detached_and_revalidated_on_every_call(self):
        child = {"type": "string", "maxLength": 16}
        schema = MappingProxyType(self.root(MappingProxyType(child)))
        first = schemas.freeze_diagnostic_schema(schema)
        child["maxLength"] = -1
        self.assertEqual(first["properties"]["item"]["maxLength"], 16)
        with self.assertRaises(ValueError):
            schemas.freeze_diagnostic_schema(schema)
        with self.assertRaises(TypeError):
            first["properties"]["item"]["maxLength"] = 99

        declaration = validate_contract(DiagnosticDeclaration("Sample", SCHEMA))
        changed = copy.deepcopy(SCHEMA)
        changed["properties"]["count"]["maximum"] = float("inf")
        object.__setattr__(declaration, "payload_schema", changed)
        with self.assertRaises(ValueError):
            validate_contract(declaration)

    def test_proxy_over_custom_mapping_still_reads_and_detaches_children(self):
        class Tracked(Mapping):
            def __init__(self, data):
                self.data = data
                self.reads = 0

            def __iter__(self):
                return iter(self.data)

            def __len__(self):
                return len(self.data)

            def __getitem__(self, key):
                self.reads += 1
                return self.data[key]

        data = self.root({"type": "string", "maxLength": 16})
        tracked = Tracked(data)
        proxy = MappingProxyType(tracked)
        first = schemas.freeze_diagnostic_schema(proxy)
        self.assertGreater(tracked.reads, 0)
        data["properties"]["item"]["unknown"] = True
        with self.assertRaises(ValueError):
            schemas.freeze_diagnostic_schema(proxy)
        self.assertNotIn("unknown", first["properties"]["item"])

    def test_exact_node_depth_and_definition_budgets_remain(self):
        groups = {
            f"group{i}": {
                "type": "object",
                "properties": {
                    f"field{j}": {"type": "string", "maxLength": 16} for j in range(15)
                },
            }
            for i in range(128)
        }
        del groups["group0"]["properties"]["field14"]
        schema = {"type": "object", "properties": groups}
        self.assertEqual(
            len(schemas.freeze_diagnostic_schema(schema)["properties"]), 128
        )
        groups["group0"]["properties"]["field14"] = {"type": "string", "maxLength": 16}
        with self.assertRaisesRegex(ValueError, "budget"):
            schemas.freeze_diagnostic_schema(schema)

        child = {"type": "string", "maxLength": 16}
        for _ in range(15):
            child = {"type": "array", "items": child, "maxItems": 1}
        schemas.freeze_diagnostic_schema(self.root(child))
        with self.assertRaisesRegex(ValueError, "budget"):
            schemas.freeze_diagnostic_schema(
                self.root({"type": "array", "items": child, "maxItems": 1})
            )
        definitions = {f"def{i}": {"type": "boolean"} for i in range(128)}
        schema = {"type": "object", "properties": {}, "$defs": definitions}
        schemas.freeze_diagnostic_schema(schema)
        definitions["extra"] = {"type": "boolean"}
        with self.assertRaisesRegex(ValueError, "definitions"):
            schemas.freeze_diagnostic_schema(schema)

    def test_refs_root_fields_and_finite_constraints_remain_checked(self):
        schema = {
            "type": "object",
            "$defs": {"text": {"type": "string", "maxLength": 16}},
            "properties": {
                "first": {"$ref": "#/$defs/text"},
                "second": {"$ref": "#/$defs/text"},
            },
        }
        frozen = schemas.freeze_diagnostic_schema(schema)
        self.assertIs(frozen["properties"]["first"], frozen["properties"]["second"])
        schema["$defs"]["text"]["maxLength"] = -1
        with self.assertRaises(ValueError):
            schemas.freeze_diagnostic_schema(schema)
        for child in (
            {"type": "string", "maxLength": 16, "$defs": {}},
            {"type": "integer", "minimum": False, "maximum": 10},
            {"type": "number", "minimum": 0, "maximum": float("inf")},
            {"type": "boolean", "unknown": True},
            {"type": "null", "maxLength": 16},
            {"type": "string", "maxLength": 16, "enum": []},
        ):
            with self.subTest(child=child), self.assertRaises(ValueError):
                schemas.freeze_diagnostic_schema(self.root(child))


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.view = SimpleNamespace(
            invocation_id="f" * 32, module_id="sample/sample", module_epoch=3
        )
        self.event = validate_contract(DiagnosticDeclaration("Sample", SCHEMA))
        self.current = True
        self.checks = 0
        self.store = DiagnosticStore()
        self.policy = DiagnosticPolicy(
            frozenset({"Sample"}), 64, event_limits={"Sample": 48}
        )

    async def check(self):
        self.checks += 1
        if not self.current:
            raise InvalidInvocation()

    def bound(self, policy=None, store=None):
        return BoundDiagnostics(
            self.view,
            (self.event,),
            self.policy if policy is None else policy,
            self.store if store is None else store,
            self.check,
        )

    async def test_identity_and_detached_payload(self):
        payload = {"count": 1, "state": "ok"}
        await self.bound().emit("Sample", payload)
        payload["count"] = 99
        event = self.store.snapshot()[0]
        self.assertEqual(
            (event["owner"], event["epoch"], event["trace"]),
            ("sample/sample", 3, "f" * 32),
        )
        self.assertEqual(event["payload"]["count"], 1)
        self.assertEqual(self.checks, 2)

    async def test_declaration_never_grants_policy(self):
        bound = BoundDiagnostics(self.view, (self.event,), None, self.store, self.check)
        with self.assertRaises(AccessDenied):
            await bound.emit("Sample", {"count": 1, "state": "ok"})
        with self.assertRaises(AccessDenied):
            await self.bound().emit("Other", {})

    async def test_closed_fields_and_secret_shapes(self):
        for payload in (
            {"count": 1, "state": "ok", "headers": {}},
            {"count": 1, "state": "Bearer secret"},
            {"count": True, "state": "ok"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ParameterError):
                await self.bound().emit("Sample", payload)
        self.assertEqual(self.store.snapshot(), [])

    async def test_limits_and_close_still_check_authority(self):
        for _ in range(60):
            await self.bound().emit("Sample", {"count": 1, "state": "ok"})
        self.assertEqual(len(self.store.snapshot()), 48)
        self.store.close()
        self.current = False
        with self.assertRaises(InvalidInvocation):
            await self.bound().emit("Sample", {"count": 1, "state": "ok"})

    async def test_sink_failure_is_optional_but_authority_is_not(self):
        def ordinary(event):
            raise RuntimeError("raw secret must never escape")

        await self.bound(store=DiagnosticStore(ordinary)).emit(
            "Sample", {"count": 1, "state": "ok"}
        )

        def revoke(event):
            self.current = False
            raise RuntimeError()

        with self.assertRaises(InvalidInvocation):
            await self.bound(store=DiagnosticStore(revoke)).emit(
                "Sample", {"count": 1, "state": "ok"}
            )
        self.current = True

        def denied(event):
            raise AccessDenied()

        with self.assertRaises(AccessDenied):
            await self.bound(store=DiagnosticStore(denied)).emit(
                "Sample", {"count": 1, "state": "ok"}
            )

    async def test_cancel_propagates(self):
        async def cancel():
            raise asyncio.CancelledError()

        bound = BoundDiagnostics(
            self.view, (self.event,), self.policy, self.store, cancel
        )
        with self.assertRaises(asyncio.CancelledError):
            await bound.emit("Sample", {"count": 1, "state": "ok"})

    def test_schema_no_external_import_or_cycle(self):
        bads = [
            {"type": "object", "properties": {}, "callback": "os.system"},
            {"type": "object", "properties": {"x": {"$ref": "https://example.com"}}},
            {
                "type": "object",
                "properties": {"x": {"$ref": "#/$defs/x"}},
                "$defs": {"x": {"$ref": "#/$defs/x"}},
            },
        ]
        for schema in bads:
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                validate_contract(DiagnosticDeclaration("Sample", schema))


class DiagnosticsRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.event = validate_contract(
            DiagnosticDeclaration(
                "Text",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string", "maxLength": 4096}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            )
        )
        self.policy = DiagnosticPolicy(frozenset({"Text"}), 64)

    def view(self, index):
        return SimpleNamespace(
            invocation_id=f"{index:032x}", module_id="sample/sample", module_epoch=1
        )

    async def check(self):
        pass

    async def emit(self, store, view, text="ok", policy=None):
        await BoundDiagnostics(
            view, (self.event,), policy or self.policy, store, self.check
        ).emit("Text", {"text": text})

    async def test_thousands_over_budget_do_not_allocate_unbounded_counters(self):
        store = DiagnosticStore()
        tiny = DiagnosticPolicy(frozenset({"Text"}), 64, max_bytes=1)
        for i in range(2000):
            await self.emit(store, self.view(i), policy=tiny)
        self.assertLessEqual(len(store._counts), 8)
        self.assertLessEqual(len(store._traces), 8)
        await self.emit(store, self.view(3000))
        self.assertEqual(len(store.snapshot()), 1)

    async def test_released_invocations_do_not_exhaust_future_emission(self):
        sink = []
        store = DiagnosticStore(sink.append)
        for i in range(2000):
            view = self.view(i)
            await self.emit(store, view)
            store.release(view)
        self.assertEqual(len(sink), 2000)
        self.assertEqual(len(store._counts), 0)
        self.assertLessEqual(len(store._traces), 8)
        self.assertLessEqual(
            sum(len(trace["events"]) for trace in store._traces.values()), 64
        )

    async def test_live_quota_survives_retention_eviction_and_admission_pressure(self):
        sink = []
        store = DiagnosticStore(sink.append)
        live = self.view(5000)
        for _ in range(64):
            await self.emit(store, live)
        for i in range(20):
            view = self.view(i)
            await self.emit(store, view)
            store.release(view)
        await self.emit(store, live)
        self.assertEqual(
            sum(event["trace"] == live.invocation_id for event in sink), 64
        )
        self.assertLessEqual(len(store._counts), 8)

    async def test_string_size_drop_preserves_other_errors_and_authority(self):
        store = DiagnosticStore()
        view = self.view(1)
        await self.emit(store, view, "x" * 4096)
        await self.emit(store, view, "x" * 4097)
        self.assertEqual(len(store.snapshot()), 1)
        with self.assertRaises(ParameterError):
            await self.emit(store, view, "Bearer secret")

        async def revoked():
            raise InvalidInvocation()

        with self.assertRaises(InvalidInvocation):
            await BoundDiagnostics(
                view, (self.event,), self.policy, store, revoked
            ).emit("Text", {"text": "x" * 4097})

    async def test_actual_issuer_release_frees_slot_and_old_handle_stays_invalid(self):
        from ygl_test_subject.core.context_issuer import ContextIssuer

        from yomihime_game_link_sdk import InvocationOrigin

        issuer = ContextIssuer()
        store = DiagnosticStore()
        store.bind_issuer(issuer)
        view = issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="sample/sample",
            module_epoch=1,
            registry_revision=1,
            actor_id="actor",
            conversation_id="chat",
            capability_id="read",
        )

        async def check():
            issuer.require(view)

        port = BoundDiagnostics(view, (self.event,), self.policy, store, check)
        await port.emit("Text", {"text": "ok"})
        self.assertEqual(len(store._counts), 1)
        issuer.release(view)
        self.assertEqual(len(store._counts), 0)
        with self.assertRaises(InvalidInvocation):
            await port.emit("Text", {"text": "ok"})
        store.close()
