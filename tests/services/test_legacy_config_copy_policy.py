import unittest
from dataclasses import replace

from ygl_test_subject.services.configuration.legacy_copy import LegacyConfigCopyPolicy

from yomihime_game_link_sdk import ConfigField, ConfigSnapshot, ConfigTarget


class LegacyConfigPolicyTests(unittest.TestCase):
    def setUp(self):
        self.source = ConfigTarget("principal", "game_link/core")
        self.target = ConfigTarget("principal", "sample/sample")
        self.field = ConfigField(
            "region", value_schema={"type": "string", "enum": ["one", "two"]}
        )
        self.policy = LegacyConfigCopyPolicy(
            "sample.region",
            self.source,
            "old_region",
            self.target.module_id,
            "region",
            self.field.value_schema,
        )

    def test_pending_only_if_target_absent(self):
        src = ConfigSnapshot(2, {"old_region": "two"}, target=self.source)
        target = ConfigSnapshot(3, {}, target=self.target)
        self.assertEqual(
            self.policy.validate(src, target, self.field), ("migration_pending", "two")
        )
        self.assertEqual(
            self.policy.validate(
                src, replace(target, values={"region": "one"}), self.field
            ),
            ("already_configured", None),
        )
        self.assertEqual(src.values, {"old_region": "two"})

    def test_missing_invalid_and_changed_declarations(self):
        target = ConfigSnapshot(3, {}, target=self.target)
        self.assertEqual(
            self.policy.validate(
                ConfigSnapshot(2, {}, target=self.source), target, self.field
            ),
            ("not_applicable", None),
        )
        for source, field in (
            (
                ConfigSnapshot(2, {"old_region": "invalid"}, target=self.source),
                self.field,
            ),
            (
                ConfigSnapshot(2, {"old_region": "one"}, target=self.source),
                replace(self.field, sensitive=True),
            ),
        ):
            with self.assertRaises(ValueError):
                self.policy.validate(source, target, field)

    def test_no_secret_field_policy(self):
        with self.assertRaises(ValueError):
            LegacyConfigCopyPolicy(
                "sample.bad",
                self.source,
                "client_secret",
                "sample/sample",
                "region",
                self.field.value_schema,
            )
