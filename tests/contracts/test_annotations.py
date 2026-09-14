"""Reflection consumers can resolve every public DTO and protocol annotation."""

import importlib
import inspect
import unittest
from typing import get_type_hints


class AnnotationIntegrationTests(unittest.TestCase):
    def test_public_contract_annotations_resolve(self):
        for part in (
            "api.contexts",
            "api.manifests",
            "api.display",
            "api.results",
            "api.storage",
            "api.services",
            "api.subscriptions",
            "api.validation",
            "core.ports",
        ):
            module = importlib.import_module("ygl_test_subject." + part)
            for name, obj in vars(module).items():
                if getattr(obj, "__module__", None) != module.__name__:
                    continue
                if inspect.isfunction(obj) or inspect.isclass(obj):
                    with self.subTest(module=part, name=name):
                        get_type_hints(obj)
                if inspect.isclass(obj):
                    for method_name, method in vars(obj).items():
                        if isinstance(method, property):
                            method = method.fget
                        if inspect.isfunction(method):
                            with self.subTest(
                                module=part, name=name, method=method_name
                            ):
                                get_type_hints(method)
