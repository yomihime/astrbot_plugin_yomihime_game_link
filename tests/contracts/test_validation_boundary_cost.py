"""Deterministic nominal-boundary cost and adversarial instance regressions."""

import asyncio
import builtins
import sys
import unittest
from abc import ABCMeta
from collections.abc import Mapping
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.contracts import validation_boundary as boundary
from ygl_test_subject.extensions.disk_manifest import parse_manifest

from yomihime_game_link_sdk import TextBlock


class ValidationBoundaryCostTests(unittest.TestCase):
    def test_catalog_requires_only_nominal_dataclasses(self):
        _, catalog = boundary._catalog()
        self.assertGreater(len(catalog), 100)
        self.assertTrue(
            all(type(kind) is type and is_dataclass(kind) for kind in catalog)
        )

        @dataclass
        class Virtual(metaclass=ABCMeta):
            value: int

        with (
            patch.object(boundary, "_VALIDATORS", None),
            patch.object(boundary, "_TYPES", None),
            patch.object(
                boundary, "import_module", return_value=SimpleNamespace(Virtual=Virtual)
            ),
            self.assertRaisesRegex(TypeError, "nominal"),
        ):
            boundary._catalog()

    def test_expanded_manifest_discrimination_does_not_scan_catalog_per_scalar(self):
        root = Path(__file__).resolve().parents[2]
        manifest = parse_manifest(
            (root / "modules/ff14/yomihime.manifest.json").read_bytes()
        )
        self.assertTrue(manifest.modules[0].diagnostics)
        source = boundary.validate_contract.__code__.co_filename
        calls = 0
        visits = 0
        discriminations = 0

        def profile(frame, event, function):
            nonlocal calls, visits, discriminations
            if frame.f_code.co_filename != source:
                return
            if event == "call" and frame.f_code.co_name == "visit":
                visits += 1
            elif event == "call" and frame.f_code.co_name == "_contract_subclass":
                discriminations += 1
            elif event == "c_call" and function is builtins.isinstance:
                calls += 1

        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            self.assertIs(boundary.validate_contract(manifest), manifest)
        finally:
            sys.setprofile(previous)
        print(
            f"BOUNDARY_CLASSIFICATION visits={visits} isinstance_calls={calls} "
            f"nominal_discriminations={discriminations}",
            flush=True,
        )
        self.assertGreater(visits, 1000)
        # A per-node constant bound, independent of machine speed/catalog size.
        # The former loop performs ~127 class checks for every scalar.
        self.assertLessEqual(calls, visits * 8)
        # Real expanded schema leaves must not pay nominal-class discrimination.
        # Count operations instead of assuming any machine-specific duration.
        self.assertLess(discriminations, visits // 2)
        # Expanded frozen-schema containers also avoid redundant nominal/ABC work.
        self.assertLess(discriminations, visits // 8)
        self.assertLess(calls, visits // 4)

    def test_every_catalog_subclass_is_rejected_even_when_nested_and_fabricated(self):
        _, catalog = boundary._catalog()
        for base in catalog:
            with self.subTest(base=base.__qualname__):
                child = type("Forged", (base,), {})
                indirect = type("IndirectForged", (child,), {})
                forged = object.__new__(indirect)
                with self.assertRaisesRegex(TypeError, "subclasses"):
                    boundary.validate_contract({"nested": [forged]})

    def test_forged_mro_and_apparent_class_do_not_escape(self):
        class HiddenMro(type):
            def __getattribute__(cls, name):
                if name == "__mro__":
                    return (object,)
                return super().__getattribute__(name)

        class Forged(TextBlock, metaclass=HiddenMro):
            pass

        class ApparentContract:
            @property
            def __class__(self):
                return TextBlock

        class ClassLike:
            __bases__ = (TextBlock,)

        class ApparentClassLike:
            @property
            def __class__(self):
                return ClassLike()

        for value in (object.__new__(Forged), ApparentContract(), ApparentClassLike()):
            with self.subTest(kind=type(value).__name__), self.assertRaises(TypeError):
                boundary.validate_contract([value])

    def test_exact_fabricated_missing_or_invalid_fields_still_reject(self):
        missing = object.__new__(TextBlock)
        invalid = object.__new__(TextBlock)
        object.__setattr__(invalid, "text", object())
        for value in (missing, invalid):
            with self.assertRaises((TypeError, ValueError)):
                boundary.validate_contract({"nested": value})

    def test_contract_cycles_and_existing_nesting_limit_still_reject(self):
        cycle = object.__new__(TextBlock)
        object.__setattr__(cycle, "text", cycle)
        with self.assertRaisesRegex(ValueError, "cycle"):
            boundary.validate_contract(cycle)
        nested = "leaf"
        for _ in range(65):
            nested = [nested]
        with self.assertRaisesRegex(ValueError, "nesting limit"):
            boundary.validate_contract(nested)

    def test_scalar_subclasses_with_apparent_contract_class_still_reject(self):
        for primitive, value in ((str, "leaf"), (int, 1), (float, 1.0), (bytes, b"x")):
            with self.subTest(primitive=primitive.__name__):

                class Apparent(primitive):
                    @property
                    def __class__(self):
                        return TextBlock

                with self.assertRaisesRegex(TypeError, "subclasses"):
                    boundary.validate_contract({"nested": [Apparent(value)]})

    def test_exact_scalar_leaves_keep_original_depth_boundary(self):
        for leaf in ("leaf", 1, 1.0, True, None, b"leaf"):
            with self.subTest(leaf_type=type(leaf).__name__):
                nested = leaf
                for _ in range(64):
                    nested = [nested]
                self.assertIs(boundary.validate_contract(nested), nested)
                with self.assertRaisesRegex(ValueError, "nesting limit"):
                    boundary.validate_contract([nested])

    def test_metaclass_hash_and_equality_cannot_impersonate_a_builtin_scalar(self):
        class ScalarImpersonator(type):
            def __hash__(cls):
                return hash(str)

            def __eq__(cls, other):
                return other is str or cls is other

        class Forged(TextBlock, metaclass=ScalarImpersonator):
            pass

        with self.assertRaisesRegex(TypeError, "subclasses"):
            boundary.validate_contract({"nested": [object.__new__(Forged)]})

    def test_container_subclasses_with_apparent_contract_class_still_reject(self):
        for container in (dict, list, tuple):
            with self.subTest(container=container.__name__):

                class Apparent(container):
                    @property
                    def __class__(self):
                        return TextBlock

                with self.assertRaisesRegex(TypeError, "subclasses"):
                    boundary.validate_contract(Apparent())

    def test_mapping_proxy_reads_custom_mapping_and_preserves_errors(self):
        class Broken(Mapping):
            def __iter__(self):
                raise RuntimeError("fixture items iteration")

            def __len__(self):
                return 1

            def __getitem__(self, key):
                raise RuntimeError("fixture item lookup")

        for value in (Broken(), MappingProxyType(Broken())):
            with self.subTest(proxy=type(value).__name__):
                with self.assertRaisesRegex(ValueError, "mapping is invalid"):
                    boundary.validate_contract(value)

        class Forged(TextBlock):
            pass

        class Backing(Mapping):
            def __iter__(self):
                return iter(("nested",))

            def __len__(self):
                return 1

            def __getitem__(self, key):
                return object.__new__(Forged)

        for value in (Backing(), MappingProxyType(Backing())):
            with self.subTest(proxy=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "subclasses"):
                    boundary.validate_contract(value)

    def test_builtin_container_hash_impersonation_still_uses_nominal_rejection(self):
        for target in (dict, list, tuple, MappingProxyType):
            with self.subTest(target=target.__name__):

                class Impersonator(type):
                    def __hash__(cls):
                        return hash(target)

                    def __eq__(cls, other):
                        return other is target or cls is other

                class Forged(TextBlock, metaclass=Impersonator):
                    pass

                with self.assertRaisesRegex(TypeError, "subclasses"):
                    boundary.validate_contract(object.__new__(Forged))

    def test_mapping_proxy_does_not_swallow_process_control(self):
        for control in (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            with self.subTest(control=control.__name__):

                class Controlled(Mapping):
                    def __iter__(self):
                        raise control()

                    def __len__(self):
                        return 1

                    def __getitem__(self, key):
                        raise control()

                with self.assertRaises(control):
                    boundary.validate_contract(MappingProxyType(Controlled()))

    def test_exact_containers_recurse_and_container_cycles_keep_depth_error(self):
        class Forged(TextBlock):
            pass

        bad = object.__new__(Forged)
        for value in (
            [bad],
            (bad,),
            {"nested": bad},
            MappingProxyType({"nested": bad}),
        ):
            with self.subTest(container=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "subclasses"):
                    boundary.validate_contract(value)
        cycle = {}
        cycle["nested"] = cycle
        with self.assertRaisesRegex(ValueError, "nesting limit"):
            boundary.validate_contract(cycle)
