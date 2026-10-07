"""Native schema and constructor snapshot validation agreement."""

import json
import unittest
from pathlib import Path

from ygl_test_subject.adapters.astrbot.web_public import (
    WebPublicRejected,
    normalize_origin,
    origin_configuration,
)
from ygl_test_subject.modules.ff14.config import (
    FF14ConfigError,
    ff14_config_snapshot,
)


class ConfigAdapterTests(unittest.TestCase):
    def test_web_origin_is_separate_and_never_disables_ordinary_config(self):
        for value in (
            "",
            "https://User:private@example.test",
            True,
            "https://example.test/query",
        ):
            config = {"web_public_origin": value, "ff14_calendar_default_days": 3}
            self.assertEqual(ff14_config_snapshot(config).calendar_default_days, 3)
            state, origin = origin_configuration(config)
            self.assertEqual(state, "unconfigured" if value == "" else "invalid")
            self.assertIsNone(origin)
        with self.assertRaises(FF14ConfigError):
            ff14_config_snapshot({"web_public_origin": "", "unknown": "private"})

    def test_single_origin_normalization_and_forbidden_components(self):
        for value, expected in (
            ("HTTPS://Example.TEST:443/", "https://example.test"),
            ("http://localhost:80", "http://localhost"),
            ("http://[::1]:7890/", "http://[::1]:7890"),
        ):
            self.assertEqual(normalize_origin(value), expected)
        for value in (
            "https://a.test/?",
            "https://a.test/#",
            "https://a.test/path",
            "https://u@a.test",
            "https://*.test",
            "null",
            "https://a.test https://b.test",
            " https://a.test",
            "https://a.test:0",
            "https://a.test\\evil",
            "file://a.test",
            "https://a.test/%2f",
        ):
            with self.subTest(value=value), self.assertRaises(WebPublicRejected):
                normalize_origin(value)

    def test_schema_defaults_match_module_snapshot_and_exclude_core_fields(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "_conf_schema.json").read_text(
                encoding="utf-8"
            )
        )
        defaults = {
            key: field["default"]
            for key, field in schema.items()
            if key != "web_public_origin"
        }
        self.assertEqual(defaults, dict(ff14_config_snapshot(None).as_values()))
        self.assertEqual(schema["ff14_default_region"]["options"], ["cn", "global"])
        self.assertTrue(all("迁移完成后本字段不再生效" in field["hint"] for name, field in schema.items() if name != "web_public_origin"))
        self.assertIn("重载", schema["web_public_origin"]["hint"])
        self.assertFalse(any("secret" in field for field in schema.values()))
        self.assertEqual(
            set(schema),
            set(ff14_config_snapshot({}).as_values()) | {"web_public_origin"},
        )
        self.assertEqual(schema["web_public_origin"]["default"], "")

    def test_adapter_rejects_untrusted_shapes_and_unknown_fields(self):
        for value in (
            [],
            "private-input",
            {"credential_fflogs_cn": "private-input"},
            {"ff14_calendar_default_days": True},
        ):
            with self.subTest(shape=type(value).__name__):
                with self.assertRaises(FF14ConfigError) as caught:
                    ff14_config_snapshot(value)
                self.assertNotIn("private-input", str(caught.exception))

    def test_constructor_value_copy_never_tracks_mutable_astrbot_mapping(self):
        config = {"ff14_calendar_default_days": 2}
        snapshot = ff14_config_snapshot(config)
        config["ff14_calendar_default_days"] = 40
        self.assertEqual(snapshot.calendar_default_days, 2)
