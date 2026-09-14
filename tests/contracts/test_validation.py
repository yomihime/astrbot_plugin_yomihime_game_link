import unittest

from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    InvocationPolicy,
)
from ygl_test_subject.api.validation import ParameterError, validate_parameters


class ParameterValidationTests(unittest.TestCase):
    def setUp(self):
        self.capability = CapabilityDescriptor(
            "records.query",
            {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "region": {
                        "type": "string",
                        "enum": ["cn", "jp"],
                        "minLength": 2,
                        "maxLength": 2,
                    },
                    "limit": {"type": "number", "minimum": 0, "maximum": 10},
                    "enabled": {"type": "boolean"},
                    "options": {"type": "object", "properties": {}},
                },
                "required": ["ids", "region"],
            },
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            CapabilityEffect.READ_ONLY,
        )

    def test_detaches_nested_inputs(self):
        supplied = {"ids": [1, 2], "region": "cn"}
        result = validate_parameters(self.capability, supplied)
        supplied["ids"].append(3)
        self.assertEqual(result["ids"], (1, 2))
        with self.assertRaises(TypeError):
            result["region"] = "jp"

    def test_rejects_wrong_types_ranges_and_closed_fields(self):
        for change in (
            {"ids": [True]},
            {"ids": ["1"]},
            {"ids": [0]},
            {"region": "us"},
            {"region": 1},
            {"enabled": 1},
            {"limit": float("nan")},
            {"limit": float("inf")},
            {"limit": 11},
            {"options": {"secret": "do-not-echo"}},
            {"extra": "do-not-echo"},
            {1: "do-not-echo"},
        ):
            with (
                self.subTest(change=change),
                self.assertRaises(ParameterError) as caught,
            ):
                validate_parameters(
                    self.capability, {"ids": [1], "region": "cn", **change}
                )
            self.assertNotIn("do-not-echo", str(caught.exception))
        for value in ({}, [], None):
            with self.subTest(value=value), self.assertRaises(ParameterError):
                validate_parameters(self.capability, value)
