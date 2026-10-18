"""Focused SDK/Core boundary and disk admission for background collectors."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import unittest
from dataclasses import replace
from types import MappingProxyType

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.extensions.disk_manifest import ManifestError, parse_manifest

from tests.contracts.test_manifests import _module, _subscription
from tests.extensions.test_disk_manifest import valid_document
from yomihime_game_link_sdk.storage import OwnershipKind
from yomihime_game_link_sdk.subscriptions import ScheduleDescriptor, ScheduleTrigger

SCHEMA = {
    "type": "object",
    "properties": {"region": {"type": "string", "enum": ["cn", "global"]}},
    "required": ["region"],
    "additionalProperties": False,
}


def descriptor(**changes):
    values = dict(
        collector_id="calendar_sync",
        key_version=1,
        source_id="calendar",
        data_version=1,
        input_schema=SCHEMA,
        shared_scope=OwnershipKind.PUBLIC,
        trigger=ScheduleTrigger.BACKGROUND,
        minimum_interval_seconds=60,
        default_interval_seconds=300,
        interval_config_key="calendar_sync_interval_seconds",
        background_parameters=({"region": "cn"},),
    )
    values.update(changes)
    return ScheduleDescriptor(**values)


class BackgroundScheduleContractTests(unittest.TestCase):
    def test_sdk_remains_descriptive_and_has_no_core_import(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import yomihime_game_link_sdk as sdk; "
                "assert sdk.ScheduleTrigger.BACKGROUND.value == 'background'; "
                "assert sdk.ScheduleDescriptor.__dataclass_fields__"
                "['background_parameters'].default == (); "
                "assert not any(x.startswith(('core', 'ygl_test_subject')) "
                "for x in sys.modules)",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate = descriptor(
            background_parameters=(), shared_scope=OwnershipKind.USER
        )
        with self.assertRaises(ValueError):
            validate_contract(candidate)

    def test_fixed_parameters_are_deeply_detached_and_immutable(self):
        source = {"values": [{"name": "first"}]}
        schema = {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                }
            },
            "required": ["values"],
        }
        result = validate_contract(
            descriptor(input_schema=schema, background_parameters=[source])
        )
        source["values"][0]["name"] = "changed"
        self.assertIsInstance(result.background_parameters, tuple)
        self.assertIsInstance(result.background_parameters[0], MappingProxyType)
        nested = result.background_parameters[0]["values"]
        self.assertIsInstance(nested, tuple)
        self.assertEqual(nested[0]["name"], "first")
        with self.assertRaises(TypeError):
            nested[0]["name"] = "forged"
        validate_contract(result)

    def test_background_requires_public_and_one_to_32_fixed_objects(self):
        for scope in OwnershipKind:
            if scope is OwnershipKind.PUBLIC:
                continue
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                validate_contract(descriptor(shared_scope=scope))
        for value in ((), tuple({"region": "cn"} for _ in range(33)), None, {}, ([],)):
            with (
                self.subTest(value=type(value)),
                self.assertRaises((TypeError, ValueError)),
            ):
                validate_contract(descriptor(background_parameters=value))
        valid = validate_contract(
            descriptor(background_parameters=tuple({"region": "cn"} for _ in range(32)))
        )
        self.assertEqual(len(valid.background_parameters), 32)
        # Equivalence after collector normalization belongs to scheduler admission.

    def test_periodic_and_on_demand_require_empty_parameters(self):
        for trigger in (ScheduleTrigger.PERIODIC, ScheduleTrigger.ON_DEMAND):
            changes = {"trigger": trigger}
            if trigger is ScheduleTrigger.ON_DEMAND:
                changes.update(default_interval_seconds=None, interval_config_key=None)
            legacy = validate_contract(descriptor(background_parameters=(), **changes))
            self.assertEqual(legacy.background_parameters, ())
            with self.assertRaises(ValueError):
                validate_contract(descriptor(**changes))

    def test_background_reuses_periodic_interval_invariants(self):
        for changes in (
            {"minimum_interval_seconds": 0},
            {"minimum_interval_seconds": True},
            {"minimum_interval_seconds": math.inf},
            {"default_interval_seconds": None},
            {"default_interval_seconds": 59},
            {"default_interval_seconds": math.nan},
            {"default_interval_seconds": True},
            {"interval_config_key": None},
            {"interval_config_key": ""},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_contract(descriptor(**changes))

    def test_fixed_parameters_obey_closed_schema_and_json_types(self):
        for value in (
            {},
            {"region": "other"},
            {"region": True},
            {"region": "cn", "x": 1},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_contract(descriptor(background_parameters=(value,)))
        for value in (math.nan, math.inf, object(), b"bytes", {1: "value"}):
            with (
                self.subTest(kind=type(value)),
                self.assertRaises((ValueError, TypeError)),
            ):
                validate_contract(
                    descriptor(background_parameters=({"region": value},))
                )

    def test_cycle_and_size_budgets_precede_unbounded_contract_visits(self):
        cycle = {}
        cycle["self"] = cycle
        deep = {}
        for _ in range(18):
            deep = {"child": deep}
        cases = (
            cycle,
            deep,
            {"text": "x" * 4097},
            {"items": list(range(257))},
            {"number": 1 << 4096},
            {"text": "\ud800"},
            {"rows": [list(range(256)) for _ in range(9)]},
            {str(index): "界" * 4096 for index in range(6)},
        )
        for value in cases:
            with (
                self.subTest(keys=tuple(value)),
                self.assertRaises(ValueError) as caught,
            ):
                validate_contract(descriptor(background_parameters=(value,)))
            self.assertNotIsInstance(caught.exception, RecursionError)

    def test_subscriptions_cannot_reference_background_collectors(self):
        schedule = validate_contract(descriptor())
        _module(schedules=(schedule,))
        with self.assertRaisesRegex(ValueError, "background collector"):
            _module(
                schedules=(schedule,),
                subscriptions=(_subscription(collector_id="calendar_sync"),),
            )
        _module(
            schedules=(
                validate_contract(
                    replace(
                        schedule,
                        trigger=ScheduleTrigger.PERIODIC,
                        background_parameters=(),
                    )
                ),
            ),
            subscriptions=(_subscription(collector_id="calendar_sync"),),
        )

    def test_disk_manifest_parses_and_detaches_background_parameters(self):
        document = valid_document()
        raw = {
            "collector_id": "calendar_sync",
            "key_version": 1,
            "source_id": "calendar",
            "data_version": 1,
            "input_schema": SCHEMA,
            "shared_scope": "public",
            "trigger": "background",
            "minimum_interval_seconds": 60,
            "default_interval_seconds": 300,
            "interval_config_key": "calendar_sync_interval_seconds",
            "background_parameters": [{"region": "cn"}, {"region": "global"}],
        }
        document["modules"][0]["schedules"] = [raw]
        parsed = parse_manifest(json.dumps(document).encode()).modules[0].schedules[0]
        self.assertIs(parsed.trigger, ScheduleTrigger.BACKGROUND)
        self.assertEqual(
            tuple(dict(v) for v in parsed.background_parameters),
            ({"region": "cn"}, {"region": "global"}),
        )
        raw["background_parameters"][0]["region"] = "changed"
        self.assertEqual(parsed.background_parameters[0]["region"], "cn")
        for parameters in ({"region": "cn"}, [], [{"region": "other"}]):
            raw["background_parameters"] = parameters
            with self.subTest(parameters=parameters), self.assertRaises(ManifestError):
                parse_manifest(json.dumps(document).encode())
