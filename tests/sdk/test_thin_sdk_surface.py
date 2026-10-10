"""Thin SDK boundary acceptance, independent of module business code."""

import importlib
import unittest


class ThinSDKSurfaceTests(unittest.TestCase):
    def test_canonical_package_and_public_conflict_identity(self):
        sdk = importlib.import_module("yomihime_game_link_sdk")
        ports = importlib.import_module("ygl_test_subject.core.ports")
        self.assertIs(sdk.RevisionConflict, ports.RevisionConflict)
        self.assertIs(sdk.UniqueConstraintViolation, ports.UniqueConstraintViolation)
        self.assertEqual(sdk.__version__, "0.1.0a6")
        for name in (
            "CacheRepository",
            "PersistedConfigPatch",
            "ModuleStoragePaths",
            "AdminOperations",
            "freeze_json",
            "validate_parameters",
        ):
            self.assertFalse(hasattr(sdk, name), name)

    def test_conflict_labels_and_exact_revisions_are_safe(self):
        sdk = importlib.import_module("yomihime_game_link_sdk")
        for label in ("", "sql/table", r"C:\secret", "secret\nvalue", "x" * 129):
            with self.subTest(label=label):
                with self.assertRaises((TypeError, ValueError)):
                    sdk.UniqueConstraintViolation(label)
        for revision in (True, -1, 1.0):
            with self.subTest(revision=revision):
                with self.assertRaises((TypeError, ValueError)):
                    sdk.RevisionConflict("record", revision, 1)
        failure = sdk.RevisionConflict("record", 1, 2)
        self.assertEqual(
            str(failure), "expected revision does not match current revision"
        )
        self.assertEqual(
            (failure.resource, failure.expected_revision, failure.actual_revision),
            ("record", 1, 2),
        )


class CoreReceivingBoundaryTests(unittest.TestCase):
    def test_sdk_construction_is_descriptive_core_checks_status_matrix(self):
        from ygl_test_subject.core.invocation import Gateway

        import yomihime_game_link_sdk as sdk

        result = sdk.CapabilityResult("bad", sdk.ResultStatus.SUCCESS)
        with self.assertRaises((ValueError, TypeError)):
            Gateway._valid_result(result, expected_privacy=sdk.Privacy.PUBLIC)

    def test_all_public_dataclass_subclasses_and_missing_fields_rejected(self):
        from dataclasses import is_dataclass

        from ygl_test_subject.core.contracts.validation_boundary import (
            validate_contract,
        )

        import yomihime_game_link_sdk as sdk

        tested = set()
        for name in sdk.__all__:
            kind = getattr(sdk, name)
            if not isinstance(kind, type) or not is_dataclass(kind) or kind in tested:
                continue
            tested.add(kind)
            with self.subTest(type=name, forgery="subclass"):
                child = type("Forged" + name, (kind,), {})
                with self.assertRaises(TypeError):
                    validate_contract(object.__new__(child))
            with self.subTest(type=name, forgery="new"):
                with self.assertRaises((TypeError, ValueError)):
                    validate_contract(object.__new__(kind))
        self.assertGreater(len(tested), 40)

    def test_nested_block_forgery_cannot_publish(self):
        from ygl_test_subject.core.invocation import Gateway

        import yomihime_game_link_sdk as sdk

        child = type("ForgedText", (sdk.TextBlock,), {})
        block = child("looks safe")
        result = sdk.CapabilityResult(
            "bad",
            sdk.ResultStatus.SUCCESS,
            sdk.DisplayDocument("title", "subject", (block,)),
        )
        with self.assertRaises(TypeError):
            Gateway._valid_result(result, expected_privacy=sdk.Privacy.PUBLIC)
        block = object.__new__(sdk.TextBlock)
        object.__setattr__(block, "text", object())
        result = sdk.CapabilityResult(
            "bad",
            sdk.ResultStatus.SUCCESS,
            sdk.DisplayDocument("title", "subject", (block,)),
        )
        with self.assertRaises((TypeError, ValueError)):
            Gateway._valid_result(result, expected_privacy=sdk.Privacy.PUBLIC)

    def test_dynamic_storage_decoder_revalidates_nested_values(self):
        import json

        from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
            _load,
        )

        for payload in (
            {
                "$type": "NumberValue",
                "fields": {"value": float("nan"), "unit": "", "precision": None},
            },
            {
                "$type": "DisplayDocument",
                "fields": {
                    "title": "title",
                    "subject": "subject",
                    "ordered_blocks": [],
                    "sources": [],
                    "timestamps": [],
                    "privacy": {"$enum": "Privacy", "value": "public"},
                    "schema_version": "9.9.0",
                },
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    _load(json.dumps(payload))

    def test_issuer_rejects_copied_and_mutated_view_as_public_error(self):
        from dataclasses import replace

        from ygl_test_subject.core.context_issuer import ContextIssuer

        import yomihime_game_link_sdk as sdk

        issuer = ContextIssuer()
        view = issuer.issue(
            origin=sdk.InvocationOrigin.COMMAND,
            module_id="sample/main",
            module_epoch=1,
            registry_revision=1,
            actor_id="actor",
            conversation_id="conversation",
        )
        for forged in (replace(view), replace(view, module_epoch=-1)):
            with self.assertRaises(sdk.InvalidInvocation):
                issuer.require(forged)
        issuer.release(view)
        with self.assertRaises(sdk.InvalidInvocation):
            issuer.require(view)
